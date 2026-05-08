import os
import datetime
import re
import requests
from playwright.sync_api import sync_playwright
from github import Github

# ==========================================
# CẤU HÌNH HỆ THỐNG
# ==========================================
TARGET_SITE = "https://bunchatv4.net/truc-tiep-bong-da-xoilac-tv"
GITHUB_TOKEN = os.getenv("GH_TOKEN") 
REPO_NAME = os.getenv("GH_REPO", "Eternal161/dausoco") 
FILE_PATH = "bongda.m3u"
WAITING_VIDEO_URL = "https://example.com/video-cho.mp4"
LIMIT_MATCHES = 30 # Giới hạn 30 trận để Actions không bị quá tải thời gian

def get_team_logo(team_name):
    """API tìm logo tự động chuẩn HD"""
    DEFAULT_LOGO = "https://cdn.sofascore.app/api/v1/team/placeholder/image"
    if not team_name or team_name == "Unknown" or len(team_name) < 3:
        return DEFAULT_LOGO
    try:
        url = f"https://www.thesportsdb.com/api/v1/json/3/searchteams.php?t={team_name}"
        response = requests.get(url, timeout=5)
        data = response.json()
        if data and data.get("teams"):
            return data["teams"][0]["strTeamBadge"]
    except:
        pass
    return DEFAULT_LOGO

def parse_url_to_info_buncha(url):
    try:
        match = re.search(r'/truc-tiep/([^/]+)', url)
        if match:
            slug = match.group(1)
            time_match = re.search(r'-(\d{4}-\d{2}-\d{2}-\d{4})$', slug)
            if time_match:
                time_str = time_match.group(1)
                thoi_gian = f"{time_str[:2]}:{time_str[2:4]} {time_str[5:].replace('-', '/')}"
                teams_part = slug.replace("-" + time_str, "")
            else:
                teams_part = slug
                thoi_gian = "Unknown"

            teams = teams_part.split('-vs-')
            doi_nha = teams[0].replace('-', ' ').title()
            doi_khach = teams[1].replace('-', ' ').title() if len(teams) > 1 else "Unknown"
            return doi_nha, doi_khach, thoi_gian
    except:
        pass
    return "Unknown", "Unknown", "Unknown"

def scrape_and_push():
    matches_data = []
    
    with sync_playwright() as p:
        # Bắt buộc headless=True khi chạy trên Github Actions
        browser = p.chromium.launch(headless=True) 
        context = browser.new_context()
        page = context.new_page()

        try:
            print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] Đang mở: {TARGET_SITE}")
            page.goto(TARGET_SITE, timeout=60000)
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(3000)

            page.evaluate("window.scrollBy(0, 800)")
            page.wait_for_timeout(2000)

            all_links = page.locator("a[href*='/truc-tiep/']").all()
            valid_matches_elements = []
            seen_hrefs = set()
            
            for el in all_links:
                href = el.get_attribute("href")
                if href and href not in seen_hrefs and "-vs-" in href:
                    seen_hrefs.add(href)
                    valid_matches_elements.append(el)

            if LIMIT_MATCHES:
                valid_matches_elements = valid_matches_elements[:LIMIT_MATCHES]
            
            print(f"=> Đang phân tích {len(valid_matches_elements)} link trận đấu...")

            for i, el in enumerate(valid_matches_elements):
                try:
                    match_url = el.get_attribute("href")
                    if match_url and not match_url.startswith("http"):
                        domain = "/".join(TARGET_SITE.split("/")[:3])
                        match_url = domain + match_url

                    doi_nha, doi_khach, thoi_gian = parse_url_to_info_buncha(match_url)
                    
                    raw_text = el.inner_text().upper()
                    try:
                        parent_text = el.locator("xpath=./ancestor::div[3]").inner_text().upper()
                        raw_text = raw_text + " " + parent_text
                    except:
                        pass
                    
                    is_live = any(kw in raw_text for kw in ["LIVE", "TRỰC TIẾP", "HIỆP", "PHÚT"])
                    status = "Đang trực tiếp" if is_live else "Chưa đá"

                    # XỬ LÝ LOGO THÔNG MINH
                    images = el.locator("img").all()
                    logo_team_a = None
                    for img in images:
                        src = img.get_attribute("src")
                        if src and "avatar" not in src.lower() and "icon" not in src.lower():
                            if not src.startswith("http"):
                                domain = "/".join(TARGET_SITE.split("/")[:3])
                                src = domain + src
                            logo_team_a = src
                            break
                            
                    if not logo_team_a or "placeholder" in logo_team_a:
                        logo_team_a = get_team_logo(doi_nha)

                    matches_data.append({
                        "title": f"{doi_nha} vs {doi_khach}",
                        "trang_thai": status,
                        "thoi_gian": thoi_gian,
                        "logo_doi_nha": logo_team_a,
                        "link_xem": match_url,
                        "luong_video": ""
                    })
                except:
                    continue

            # --- GIAI ĐOẠN 2: BẮT LUỒNG (ANTI-ADS) ---
            live_matches = [m for m in matches_data if m["trang_thai"] == "Đang trực tiếp"]
            print(f"\n=> Đã chốt {len(live_matches)} trận Đang trực tiếp. Bắt đầu dò luồng mạng...")

            for match in live_matches:
                print(f"--- Dò luồng: {match['title']} ---")
                video_streams = []
                
                def request_handler(request):
                    if ".m3u8" in request.url or ".flv" in request.url:
                        video_streams.append(request.url)
                
                page.on("request", request_handler)
                
                try:
                    page.goto(match["link_xem"], timeout=60000)
                    page.wait_for_timeout(3000)
                    
                    viewport_size = page.viewport_size
                    if viewport_size:
                        page.mouse.click(viewport_size['width']/2, viewport_size['height']/2)

                        try:
                            skip_btn = page.locator("xpath=//*[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'bỏ qua') or contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'skip')]").first
                            skip_btn.wait_for(state="visible", timeout=10000)
                            skip_btn.click()
                            video_streams.clear() 
                            page.wait_for_timeout(2000) 
                        except:
                            pass 

                    page.wait_for_timeout(8000) 
                except:
                    pass
                
                page.remove_listener("request", request_handler)
                clean_streams = [s for s in video_streams if "ads" not in s.lower() and "quangcao" not in s.lower()]
                
                if clean_streams:
                    match["luong_video"] = clean_streams[-1]
                    print(f"  => BẮT LUỒNG THÀNH CÔNG!")
                else:
                    print("  => Web chưa tung luồng.")

        except Exception as e:
            print(f"Lỗi hệ thống: {e}")
        finally:
            browser.close()

    # --- GIAI ĐOẠN 3: TẠO M3U VÀ ĐẨY LÊN GITHUB ---
    if not matches_data:
        print("Không cào được dữ liệu.")
        return

    m3u_text = "#EXTM3U\n"
    for match in matches_data:
        stream = match["luong_video"] if match["luong_video"] else WAITING_VIDEO_URL
        m3u_text += f'#EXTINF:-1 tvg-logo="{match["logo_doi_nha"]}" group-title="Bún Chả TV", {match["title"]} - {match["thoi_gian"]} ({match["trang_thai"]})\n'
        m3u_text += f'{stream}\n\n'

    if GITHUB_TOKEN:
        try:
            g = Github(GITHUB_TOKEN)
            repo = g.get_repo(REPO_NAME)
            commit_msg = f"Cập nhật luồng M3U8: {datetime.datetime.now().strftime('%H:%M %d/%m/%Y')}"
            
            try:
                contents = repo.get_contents(FILE_PATH)
                repo.update_file(contents.path, commit_msg, m3u_text, contents.sha)
                print(f"=> THÀNH CÔNG: Đã ghi đè {FILE_PATH} lên GitHub!")
            except:
                repo.create_file(FILE_PATH, commit_msg, m3u_text)
                print(f"=> THÀNH CÔNG: Đã tạo mới {FILE_PATH} trên GitHub!")
        except Exception as e:
            print(f"[!] Lỗi kết nối GitHub: {e}")
    else:
        print("[!] Không tìm thấy GH_TOKEN trong môi trường.")

if __name__ == "__main__":
    scrape_and_push()
