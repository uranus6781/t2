import os
import time
import re
import json
import hashlib
import traceback
from github import Github, Auth
from seleniumwire import webdriver 
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# ================= CẤU HÌNH CƠ BẢN =================
GITHUB_TOKEN = os.environ.get("MY_GITHUB_TOKEN") 
GITHUB_REPO_NAME = "Eternal161/hoiquan" 
GITHUB_FILE_PATH = "playlist.json"
BACKGROUND_IMG = "https://imgur.com/HDRH6Ii" # DÁN LINK ẢNH NỀN CỦA BẠN VÀO ĐÂY
# ===================================================

def init_driver():
    options = webdriver.ChromeOptions()
    options.add_argument('--headless')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    return webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)

def get_m3u8_link(driver, url):
    """Hàm chuyên dụng: Vào trang xem trực tiếp và rình lấy đúng link m3u8 của trận đó"""
    del driver.requests # Dọn sạch rác mạng của trận trước (CHỐNG TRÙNG LINK)
    driver.get(url)
    
    max_wait_time = 15 # Đợi tối đa 15 giây
    start_time = time.time()
    
    while time.time() - start_time < max_wait_time:
        for req in driver.requests:
            if req.response and '.m3u8' in req.url:
                # Bỏ qua các link m3u8 không phải là luồng stream chính
                if 'chunklist' not in req.url and 'ad' not in req.url:
                    return req.url
        time.sleep(1) # Quét mỗi giây 1 lần
        
    return "http://link_khong_ton_tai_hoac_chua_phat.m3u8"

def make_absolute_url(url):
    if not url: return BACKGROUND_IMG
    if url.startswith("//"): return "https:" + url
    if url.startswith("/"): return "https://hoiquan1.live" + url
    return url

def main():
    driver = init_driver()
    
    # Cấu trúc JSON chuẩn cho Mon Player
    du_lieu_json = {
        "id": "hoiquan-tv-pro",
        "url": f"https://raw.githack.com/{GITHUB_REPO_NAME}/main/{GITHUB_FILE_PATH}",
        "name": "Trực Tiếp Bóng Đá",
        "color": "#1cb57a",
        "grid_number": 3,
       "image": {
        "type": "cover", 
        "url": "https://i.postimg.cc/02tKjcyN/JT3IVCOJDKW3PBRFZAZUILENLU.jpg"
    }},

    # Hai mảng chứa riêng biệt
    live_channels = []
    upcoming_channels = []
    link_da_quet = set()

    try:
        wait = WebDriverWait(driver, 15)
        driver.get("https://sv2.hoiquan2.live/lich-thi-dau/bong-da")
        items = wait.until(EC.presence_of_all_elements_located((By.CSS_SELECTOR, "a[href*='bong-da']")))
        
        matches_data = []

        # BƯỚC 1: LẤY THÔNG TIN CƠ BẢN CỦA TẤT CẢ CÁC TRẬN
        for item in items:
            link = item.get_attribute("href")
            if link in link_da_quet: continue
            link_da_quet.add(link)
            
            text = item.text
            lines = [l.strip() for l in text.split('\n') if l.strip()]
            if not lines: continue
            
            giai_dau = lines[0].upper()
            teams = item.find_elements(By.CSS_SELECTOR, "span.truncate")
            if len(teams) < 2: continue
            doi_1, doi_2 = teams[0].text.strip(), teams[1].text.strip()

            # Lấy Logo
            html_content = item.get_attribute("innerHTML")
            all_urls = re.findall(r'src="([^"]+)"', html_content) + re.findall(r'url\([\'"]?(.*?)[\'"]?\)', html_content)
            real_logos = [make_absolute_url(u) for u in all_urls if "bg-fixture" not in u and "data:image" not in u]
            # Loại bỏ trùng lặp giữ nguyên thứ tự
            real_logos = list(dict.fromkeys(real_logos))
            
            logo_1 = real_logos[0] if len(real_logos) > 0 else BACKGROUND_IMG
            logo_2 = real_logos[1] if len(real_logos) > 1 else logo_1

            # Tách Tỉ số và Thời gian
            score_match = re.search(r"(\d+)\s*-\s*(\d+)", text)
            ti_so = f"{score_match.group(1)} - {score_match.group(2)}" if score_match else "0 - 0"

            time_match = re.search(r"(\d{2}:\d{2})\s*[\r\n]*\s*(\d{2}/\d{2}/\d{4})?", text)
            if time_match:
                gio = time_match.group(1)
                ngay = time_match.group(2) if time_match.group(2) else ""
                thoi_gian = f"{gio} {ngay}".strip()
            else:
                thoi_gian = "Đang cập nhật"

            # Xác định Live hay Sắp diễn ra
            text_upper = text.upper()
            is_finished = "FT" in text_upper or "KT" in text_upper or "HẾT GIỜ" in text_upper
            is_live = (bool(score_match) and not is_finished) or (("LIVE" in text_upper or "ĐANG ĐÁ" in text_upper) and not is_finished)

            if not is_finished: # Chỉ lấy những trận chưa kết thúc
                matches_data.append({
                    "link": link, "giai": giai_dau, "doi_1": doi_1, "doi_2": doi_2,
                    "logo_1": logo_1, "logo_2": logo_2, "ti_so": ti_so,
                    "thoi_gian": thoi_gian, "is_live": is_live
                })

        # BƯỚC 2: CÀO M3U8 VÀ ĐÓNG GÓI JSON CHO TỪNG TRẬN
        for tran in matches_data:
            link_m3u8 = "http://waiting.m3u8"
            
            # Chỉ tốn thời gian cào m3u8 cho các trận ĐANG ĐÁ
            if tran['is_live']:
                link_m3u8 = get_m3u8_link(driver, tran['link'])
                nhan_hien_thi = f"🔴 LIVE | {tran['thoi_gian']}"
                label_color = "#e50914" # Màu đỏ
            else:
                nhan_hien_thi = f"⏳ Sắp diễn ra | {tran['thoi_gian']}"
                label_color = "#1cb57a" # Màu xanh lá

            match_id = "hq-" + hashlib.md5(f"{tran['doi_1']}{tran['doi_2']}".encode()).hexdigest()[:8]
            
            kenh_json = {
                "id": match_id,
                "name": f"🏆 {tran['giai']} | ⚽ {tran['doi_1']} vs {tran['doi_2']}",
                "type": "single",
                "display": "default",
                "enable_detail": False,  
                "image": {
                    "padding": 0,
                    "background_color": "#000000",
                    "display": "cover",
                    "url": BACKGROUND_IMG, 
                    "width": 1600,
                    "height": 900
                },
                "labels": [{"text": nhan_hien_thi, "position": "top-left", "color": label_color, "text_color": "#ffffff"}],
                "sources": [{
                    "id": f"src-{match_id}",
                    "name": "Nguồn Phóng",
                    "contents": [{
                        "id": f"ct-{match_id}",
                        "name": f"{tran['doi_1']} vs {tran['doi_2']}",
                        "streams": [{
                            "id": f"st-{match_id}",
                            "name": "Server Siêu Mượt",
                            "stream_links": [{
                                "id": f"lnk-{match_id}",
                                "name": "Bấm Để Xem",
                                "type": "hls",
                                "default": True,
                                "url": link_m3u8,
                                "request_headers": [
                                    {"key": "Referer", "value": "https://hoiquan1.live/"},
                                    {"key": "User-Agent", "value": "Mozilla/5.0"}
                                ]
                            }]
                        }]
                    }]
                }],
                "org_metadata": {
                    "league": tran['giai'],
                    "team_a": tran['doi_1'],
                    "team_b": tran['doi_2'],
                    "logo_a": tran['logo_1'],
                    "logo_b": tran['logo_2'],
                    "score": tran['ti_so'],
                    "thumb": BACKGROUND_IMG
                }
            }

            if tran['is_live']:
                live_channels.append(kenh_json)
            else:
                upcoming_channels.append(kenh_json)

        # Đưa vào Group
        if live_channels:
            du_lieu_json["groups"].append({
                "id": "group-live", "name": "🔴 ĐANG DIỄN RA", "display": "vertical", "grid_number": 3,
                "enable_detail": False, "channels": live_channels
            })
        if upcoming_channels:
            du_lieu_json["groups"].append({
                "id": "group-upcoming", "name": "⏳ SẮP DIỄN RA", "display": "vertical", "grid_number": 3,
                "enable_detail": False, "channels": upcoming_channels
            })

        # BƯỚC 3: ĐẨY LÊN GITHUB
        if GITHUB_TOKEN:
            auth = Auth.Token(GITHUB_TOKEN)
            g = Github(auth=auth)
            repo = g.get_repo(GITHUB_REPO_NAME)
            json_content = json.dumps(du_lieu_json, ensure_ascii=False, indent=4)
            
            try:
                contents = repo.get_contents(GITHUB_FILE_PATH)
                repo.update_file(contents.path, "Tự động phân loại trận đấu & Vá lỗi m3u8", json_content, contents.sha)
                print("Đã cập nhật GitHub thành công!")
            except Exception:
                repo.create_file(GITHUB_FILE_PATH, "Tạo mới playlist", json_content)
                print("Đã tạo file mới trên GitHub!")

    except Exception:
        traceback.print_exc()
    finally:
        driver.quit()

if __name__ == "__main__":
    main()

