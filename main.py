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
LIMIT_MATCHES = 30

# ==========================================
# HÀM LẤY LOGO
# ==========================================
def get_team_logo(team_name):
    """Lấy logo từ nhiều nguồn, ưu tiên CDN tương thích MonPlayer"""
    if not team_name or team_name == "Unknown" or len(team_name) < 3:
        return "https://upload.wikimedia.org/wikipedia/commons/thumb/1/1b/Football_%28soccer_ball%29.svg/1200px-Football_%28soccer_ball%29.svg.png"
    
    # Chuẩn hóa tên team cho API
    team_slug = team_name.lower().replace(' ', '-')
    team_encoded = requests.utils.quote(team_name)
    
    # Danh sách các nguồn logo
    sources = [
        # Nguồn 1: API-Football (CDN nhanh nhất)
        f"https://media.api-sports.io/football/teams/{team_slug}.png",
        
        # Nguồn 2: TheSportsDB
        f"https://www.thesportsdb.com/api/v1/json/3/searchteams.php?t={team_encoded}",
    ]
    
    # Thử nguồn trực tiếp trước
    try:
        response = requests.head(sources[0], timeout=3, allow_redirects=True)
        if response.status_code == 200:
            return sources[0]
    except:
        pass
    
    # Thử nguồn TheSportsDB
    try:
        response = requests.get(sources[1], timeout=5)
        data = response.json()
        if data and data.get("teams"):
            badge = data["teams"][0].get("strTeamBadge")
            if badge and "placeholder" not in badge.lower() and badge.startswith("http"):
                return badge
    except:
        pass
    
    # Fallback: Tạo logo từ tên team
    return f"https://ui-avatars.com/api/?name={team_encoded}&size=256&background=random&color=fff&bold=true&format=png"

def validate_and_fix_logo_url(url):
    """Kiểm tra và sửa URL logo cho tương thích với MonPlayer"""
    if not url:
        return "https://via.placeholder.com/256/333/fff?text=N/A"
    
    # Đảm bảo URL bắt đầu với https
    if not url.startswith('http'):
        url = 'https:' + url if url.startswith('//') else 'https://' + url
    
    # Xử lý ký tự đặc biệt
    url = url.replace(' ', '%20')
    
    # Kiểm tra URL khả dụng (cache 1 giờ)
    try:
        response = requests.head(url, timeout=3, allow_redirects=True)
        if response.status_code != 200:
            return "https://via.placeholder.com/256/333/fff?text=Error"
    except:
        return "https://via.placeholder.com/256/333/fff?text=Error"
    
    return url

# ==========================================
# HÀM PARSE THÔNG TIN TRẬN ĐẤU
# ==========================================
def parse_url_to_info_buncha(url):
    """Parse URL để lấy thông tin trận đấu"""
    try:
        match = re.search(r'/truc-tiep/([^/]+)', url)
        if match:
            slug = match.group(1)
            
            # Tìm pattern thời gian: 2024-01-15-1930
            time_match = re.search(r'-(\d{4}-\d{2}-\d{2}-\d{4})$', slug)
            if time_match:
                time_str = time_match.group(1)
                # Format: "19:30 2024/01/15"
                clock = f"{time_str[11:13]}:{time_str[13:15]}"
                date = f"{time_str[0:4]}/{time_str[5:7]}/{time_str[8:10]}"
                thoi_gian = f"{clock} {date}"
                teams_part = slug[:slug.rfind('-' + time_str)]
            else:
                teams_part = slug
                thoi_gian = "Chưa có lịch"

            # Tách tên 2 đội
            teams = teams_part.split('-vs-')
            doi_nha = teams[0].replace('-', ' ').title()
            doi_khach = teams[1].replace('-', ' ').title() if len(teams) > 1 else "Unknown"
            
            return doi_nha, doi_khach, thoi_gian
    except Exception as e:
        print(f"  [!] Lỗi parse URL: {e}")
    
    return "Unknown", "Unknown", "Unknown"

# ==========================================
# HÀM TẠO M3U CHO MONPLAYER
# ==========================================
def create_m3u_for_monplayer(matches_data):
    """Tạo file M3U tối ưu cho MonPlayer với logo đầy đủ"""
    m3u_lines = ['#EXTM3U']
    m3u_lines.append('#PLAYLIST:Bóng Đá Trực Tiếp - Bún Chả TV')
    m3u_lines.append('')
    
    for idx, match in enumerate(matches_data, 1):
        stream = match.get("luong_video") or WAITING_VIDEO_URL
        
        # Validate và fix logo URLs
        logo_home = validate_and_fix_logo_url(match.get("logo_doi_nha"))
        logo_away = validate_and_fix_logo_url(match.get("logo_doi_khach"))
        
        # Format thời gian
        time_display = match.get("thoi_gian", "Chưa có lịch")
        
        # Icon trạng thái
        status = match.get("trang_thai", "")
        if "trực tiếp" in status.lower():
            status_icon = "🔴"
        elif "chưa đá" in status.lower():
            status_icon = "⏰"
        else:
            status_icon = "⚽"
        
        # Tên hiển thị
        display_name = f"{status_icon} {match['title']} - {time_display} ({status})"
        
        # EXTINF với metadata cho MonPlayer
        extinf = (
            f'#EXTINF:-1 '
            f'tvg-id="{idx}" '
            f'tvg-name="{match["title"]}" '
            f'tvg-logo="{logo_home}" '
            f'group-title="⚽ Bóng Đá Trực Tiếp" '
            f',{display_name}'
        )
        
        m3u_lines.append(extinf)
        m3u_lines.append(f'#EXTIMG:{logo_home}')
        m3u_lines.append(stream)
        m3u_lines.append('')
    
    return '\n'.join(m3u_lines)

# ==========================================
# HÀM CHÍNH SCRAPE
# ==========================================
def scrape_and_push():
    matches_data = []
    
    print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] Bắt đầu quét dữ liệu...")
    print("=" * 60)
    
    with sync_playwright() as p:
        # Cấu hình browser cho GitHub Actions
        browser = p.chromium.launch(
            headless=True,
            args=[
                '--no-sandbox',
                '--disable-setuid-sandbox',
                '--disable-dev-shm-usage',
                '--disable-accelerated-2d-canvas',
                '--disable-gpu',
                '--disable-web-security',
                '--disable-features=IsolateOrigins,site-per-process'
            ]
        )
        
        context = browser.new_context(
            viewport={'width': 1920, 'height': 1080},
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        )
        page = context.new_page()
        
        try:
            # === PHASE 1: LẤY DANH SÁCH TRẬN ĐẤU ===
            print(f"[1/3] Đang mở trang: {TARGET_SITE}")
            page.goto(TARGET_SITE, timeout=60000)
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(5000)
            
            # Scroll để load content
            page.evaluate("window.scrollBy(0, 800)")
            page.wait_for_timeout(2000)
            
            # Tìm tất cả link trận đấu
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
            
            print(f"[1/3] Tìm thấy {len(valid_matches_elements)} trận đấu")
            print("=" * 60)
            
            # === PHASE 2: PARSE THÔNG TIN TỪNG TRẬN ===
            print(f"[2/3] Đang phân tích thông tin trận đấu...")
            
            for i, el in enumerate(valid_matches_elements):
                try:
                    match_url = el.get_attribute("href")
                    if match_url and not match_url.startswith("http"):
                        domain = "/".join(TARGET_SITE.split("/")[:3])
                        match_url = domain + match_url
                    
                    # Parse thông tin cơ bản
                    doi_nha, doi_khach, thoi_gian = parse_url_to_info_buncha(match_url)
                    
                    # Kiểm tra trạng thái
                    raw_text = el.inner_text().upper()
                    try:
                        parent_text = el.locator("xpath=./ancestor::div[3]").inner_text().upper()
                        raw_text = raw_text + " " + parent_text
                    except:
                        pass
                    
                    is_live = any(kw in raw_text for kw in ["LIVE", "TRỰC TIẾP", "HIỆP", "PHÚT"])
                    status = "Đang trực tiếp" if is_live else "Chưa đá"
                    
                    # Lấy logo từ trang web
                    images = el.locator("img").all()
                    web_logos = []
                    
                    for img in images:
                        src = img.get_attribute("src")
                        alt = img.get_attribute("alt") or ""
                        
                        if src and ("team" in alt.lower() or "logo" in alt.lower() or 
                                   "flag" in alt.lower() or "icon" in src.lower()):
                            if not src.startswith("http"):
                                domain = "/".join(TARGET_SITE.split("/")[:3])
                                src = domain + src
                            web_logos.append(src)
                            if len(web_logos) >= 2:
                                break
                    
                    # Gán logo (ưu tiên từ web, fallback về API)
                    if len(web_logos) >= 2:
                        logo_doi_nha = web_logos[0]
                        logo_doi_khach = web_logos[1]
                    elif len(web_logos) == 1:
                        logo_doi_nha = web_logos[0]
                        logo_doi_khach = get_team_logo(doi_khach)
                    else:
                        logo_doi_nha = get_team_logo(doi_nha)
                        logo_doi_khach = get_team_logo(doi_khach)
                    
                    matches_data.append({
                        "title": f"{doi_nha} vs {doi_khach}",
                        "trang_thai": status,
                        "thoi_gian": thoi_gian,
                        "logo_doi_nha": logo_doi_nha,
                        "logo_doi_khach": logo_doi_khach,
                        "link_xem": match_url,
                        "luong_video": ""
                    })
                    
                    print(f"  [{i+1}/{len(valid_matches_elements)}] {doi_nha} vs {doi_khach}")
                    print(f"       Thời gian: {thoi_gian} | Trạng thái: {status}")
                    print(f"       Logo: {logo_doi_nha[:50]}...")
                    
                except Exception as e:
                    print(f"  [!] Lỗi xử lý trận {i+1}: {e}")
                    continue
            
            # === PHASE 3: BẮT LUỒNG VIDEO ===
            live_matches = [m for m in matches_data if m["trang_thai"] == "Đang trực tiếp"]
            print("\n" + "=" * 60)
            print(f"[3/3] Bắt luồng video cho {len(live_matches)} trận đang trực tiếp...")
            
            for idx, match in enumerate(live_matches):
                print(f"\n  [{idx+1}/{len(live_matches)}] Đang dò luồng: {match['title']}")
                video_streams = []
                
                def request_handler(request):
                    if ".m3u8" in request.url or ".flv" in request.url:
                        video_streams.append(request.url)
                        print(f"       Phát hiện stream: {request.url[:80]}...")
                
                try:
                    page.on("request", request_handler)
                    page.goto(match["link_xem"], timeout=30000)
                    page.wait_for_timeout(3000)
                    
                    # Click vào giữa màn hình để kích hoạt player
                    viewport_size = page.viewport_size
                    if viewport_size:
                        page.mouse.click(viewport_size['width']/2, viewport_size['height']/2)
                    
                    # Thử skip quảng cáo
                    try:
                        for skip_text in ["Bỏ qua", "Skip", "Skip Ad"]:
                            skip_btn = page.get_by_text(skip_text, exact=False).first
                            if skip_btn.is_visible(timeout=3000):
                                skip_btn.click()
                                video_streams.clear()
                                page.wait_for_timeout(2000)
                                break
                    except:
                        pass
                    
                    # Đợi video load
                    page.wait_for_timeout(10000)
                    
                    # Lọc stream sạch
                    clean_streams = [s for s in video_streams 
                                   if not any(ad in s.lower() for ad in ["ads", "quangcao", "advertisement", "doubleclick"])]
                    
                    if clean_streams:
                        match["luong_video"] = clean_streams[-1]
                        print(f"       ✓ BẮT LUỒNG THÀNH CÔNG!")
                    else:
                        print(f"       ✗ Không tìm thấy luồng video phù hợp")
                        
                except Exception as e:
                    print(f"       ✗ Lỗi: {e}")
                finally:
                    page.remove_listener("request", request_handler)
                    page.wait_for_timeout(1000)
            
            print("\n" + "=" * 60)
            
        except Exception as e:
            print(f"[!] Lỗi hệ thống: {e}")
        finally:
            browser.close()
    
    # === TẠO FILE M3U VÀ PUSH LÊN GITHUB ===
    if not matches_data:
        print("[!] Không cào được dữ liệu. Dừng script.")
        return
    
    print("\nĐang tạo file M3U cho MonPlayer...")
    m3u_text = create_m3u_for_monplayer(matches_data)
    
    # In mẫu để debug
    print("\n--- MẪU M3U (3 trận đầu) ---")
    lines = m3u_text.split('\n')
    count = 0
    for line in lines:
        if line.startswith('#EXTINF'):
            print(line[:120] + "...")
            count += 1
            if count >= 3:
                break
    print("----------------------------\n")
    
    # Push lên GitHub
    if GITHUB_TOKEN:
        try:
            g = Github(GITHUB_TOKEN)
            repo = g.get_repo(REPO_NAME)
            commit_msg = f"⚽ Cập nhật luồng: {datetime.datetime.now().strftime('%H:%M %d/%m/%Y')} - {len(matches_data)} trận"
            
            try:
                contents = repo.get_contents(FILE_PATH)
                repo.update_file(contents.path, commit_msg, m3u_text, contents.sha)
                print(f"✓ ĐÃ CẬP NHẬT: {FILE_PATH} trên GitHub!")
            except:
                repo.create_file(FILE_PATH, commit_msg, m3u_text)
                print(f"✓ ĐÃ TẠO MỚI: {FILE_PATH} trên GitHub!")
                
            print(f"✓ Tổng số trận: {len(matches_data)}")
            print(f"✓ Trận đang live: {sum(1 for m in matches_data if m['trang_thai'] == 'Đang trực tiếp')}")
            print(f"✓ Có luồng video: {sum(1 for m in matches_data if m['luong_video'])}")
            
        except Exception as e:
            print(f"[!] Lỗi push GitHub: {e}")
    else:
        print("[!] KHÔNG TÌM THẤY GH_TOKEN - Lưu file local...")
        with open(FILE_PATH, 'w', encoding='utf-8') as f:
            f.write(m3u_text)
        print(f"✓ Đã lưu local: {FILE_PATH}")

if __name__ == "__main__":
    scrape_and_push()
