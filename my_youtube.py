import sys
import time
import logging
import os
import pickle
from datetime import datetime
import google_auth_oauthlib.flow
import googleapiclient.discovery
import googleapiclient.errors
from google.auth.transport.requests import Request

# --- КОНФІГУРАЦІЯ ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
CLIENT_SECRETS_FILE = "client_secrets.json"
SCOPES = ["https://www.googleapis.com/auth/youtube.force-ssl"]
MY_PLAYLIST_ID = "PL2oOQvhc23H6GBf8GTTu2fxldVfdJmQz1" # musica2

def get_authenticated_service():
    """Авторизація через токен."""
    credentials = None
    if os.path.exists("token.pickle"):
        with open("token.pickle", "rb") as token:
            credentials = pickle.load(token)
    if not credentials or not credentials.valid:
        if credentials and credentials.expired and credentials.refresh_token:
            credentials.refresh(Request())
        else:
            flow = google_auth_oauthlib.flow.InstalledAppFlow.from_client_secrets_file(
                CLIENT_SECRETS_FILE, SCOPES)
            credentials = flow.run_local_server(port=0)
        with open("token.pickle", "wb") as token:
            pickle.dump(credentials, token)
    return googleapiclient.discovery.build("youtube", "v3", credentials=credentials)

def get_recent_watched_videos(youtube):
    """Отримує ID відео безпосередньо з системного плейліста історії (HL)."""
    watched_video_ids = []
    print("--- Крок 1: Отримання історії (через History List) ---")
    
    try:
        # Спочатку дізнаємося ID вашого плейліста історії
        channels_response = youtube.channels().list(
            part="contentDetails",
            mine=True
        ).execute()
        
        # ID історії зазвичай не віддається прямо, тому ми використовуємо 
        # перевірений метод отримання через activities, але з іншим підходом,
        # або пробуємо отримати "HL" плейліст.
        # Але оскільки Google обмежив прямий доступ до HL, ми використаємо
        # 'playlistItems' для спеціального коду історії, якщо це можливо, 
        # або повернемося до 'activities', але БЕЗ фільтрації mine=True, 
        # яка часто багує.
        
        request = youtube.activities().list(
            part="snippet,contentDetails",
            mine=True,
            maxResults=50
        )
        response = request.execute()
        
        # Якщо activities все одно порожній, спробуємо отримати список 'uploads' 
        # вашого каналу, щоб перевірити, чи взагалі є зв'язок з API
        if not response.get("items"):
            print("ПОПЕРЕДЖЕННЯ: activities порожній. Перевірте, чи не вимкнено запис історії в налаштуваннях Google!")
            return []

        for item in response.get("items", []):
            details = item.get("contentDetails", {})
            v_id = None
            
            # Шукаємо videoId у всіх можливих полях
            if "watch" in details: v_id = details["watch"].get("videoId")
            elif "playlistItem" in details: v_id = details["playlistItem"].get("resourceId", {}).get("videoId")
            
            if v_id:
                watched_video_ids.append(v_id)
                
        unique_ids = list(dict.fromkeys(watched_video_ids))
        print(f"Знайдено {len(unique_ids)} відео в історії.")
        return unique_ids

    except Exception as e:
        print(f"Помилка доступу до історії: {e}")
        return []

def move_watched_to_end(youtube, playlist_id):
    """Основна логіка переміщення."""
    # Очищуємо ID від сміття
    playlist_id = playlist_id.split('&')[0]
    
    watched_ids = get_recent_watched_videos(youtube)
    if not watched_ids:
        print("Не вдалося знайти відео в історії.")
        return

    print(f"\n--- Крок 2: Сканування плейліста {playlist_id} ---")
    
    to_move = []
    next_page_token = None
    total_scanned = 0

    while True:
        try:
            request = youtube.playlistItems().list(
                part="id,snippet,contentDetails",
                playlistId=playlist_id,
                maxResults=50,
                pageToken=next_page_token
            )
            response = request.execute()
            items = response.get("items", [])
            total_scanned += len(items)
            
            for item in items:
                v_id = item["contentDetails"]["videoId"]
                if v_id in watched_ids:
                    to_move.append({
                        'item_id': item['id'],
                        'video_id': v_id,
                        'title': item['snippet']['title']
                    })
            
            print(f"Проскановано {total_scanned} відео...", end="\r")
            
            next_page_token = response.get("nextPageToken")
            if not next_page_token:
                break
        except Exception as e:
            print(f"\nПомилка при читанні плейліста: {e}")
            break

    print(f"\nСканування завершено. Знайдено збігів для ротації: {len(to_move)}")

    if not to_move:
        print("Жодне відео з останньої історії не знайдено в плейлісті.")
        return
    
    if to_move:
        print('Переносимо? y/n')
        answer = input()

    if answer == 'n':
        sys.exit()

    if answer == 'y':
        print("\n--- Крок 3: Переміщення в кінець ---")
        moved_count = 0
        for video in to_move:
            try:
                print(f"[{moved_count+1}/{len(to_move)}] Ротую: {video['title']}")
                
                # Видаляємо
                youtube.playlistItems().delete(id=video['item_id']).execute()
                
                # Додаємо в кінець
                youtube.playlistItems().insert(
                    part="snippet",
                    body={
                        "snippet": {
                            "playlistId": playlist_id,
                            "resourceId": {"kind": "youtube#video", "videoId": video['video_id']}
                        }
                    }
                ).execute()
                moved_count += 1
                
            except googleapiclient.errors.HttpError as e:
                if e.resp.status == 403:
                    print("\n!!! Квота вичерпана. Спробуйте завтра.")
                    return
                else:
                    print(f"Помилка з {video['title']}: {e}")

        print(f"\nГотово! Переміщено відео: {moved_count}")

if __name__ == "__main__":
    os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"
    while True:
        try:
            service = get_authenticated_service()
            move_watched_to_end(service, MY_PLAYLIST_ID)
            logging.info("Завдання виконано успішно. Наступний запуск через годину.")
        except Exception as e:
            logging.error(f"Виникла помилка: {e}")
            logging.info("Спробуємо ще раз через 5 хвилин...")
            time.sleep(300) # Якщо помилка, чекаємо менше
            continue 


