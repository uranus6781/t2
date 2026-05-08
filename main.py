import os
import datetime
import re
import requests
import json
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
LIMIT_MATCHES = 3

# Cache logo để tránh gọi API nhiều lần
LOGO_CACHE = {}

# ==========================================
# HÀM LẤY LOGO (ĐÃ SỬA)
# ==========================================
def get_team_logo(team_name):
    """Lấy logo từ cache hoặc API"""
    if not team_name or team_name == "Unknown" or len(team_name) < 3:
        return "https://upload.wikimedia.org/wikipedia/commons/thumb/1/1b/Football_%28soccer_ball%29.svg/1200px-Football_%28soccer_ball%29.svg.png"
    
    # Kiểm tra cache
    if team_name in LOGO_CACHE:
        return LOGO_CACHE[team_name]
    
    # Chuẩn hóa tên
    team_slug = team_name.lower().replace(' ', '-')
    
    # Danh sách nguồn logo (theo thứ tự ưu tiên)
    logo_sources = [
        # Nguồn 1: API Football (nhanh nhất)
        {
            "url": f"https://media.api-sports.io/football/teams/{team_slug}.png",
            "type": "direct"
        },
        # Nguồn 2: Tạo logo từ tên team (đảm bảo luôn có)
        {
            "url": f"https://ui-avatars.com/api/?name={requests.utils.quote(team_name)}&size=256&background=1a73e8&color=fff&bold=true&format=png",
            "type": "direct"
        }
    ]
    
    for source in logo_sources:
        try:
            if source["type"] == "direct":
                response = requests.head(source["url"], timeout=3, allow_redirects=True)
                if response.status_code == 200:
                    LOGO_CACHE[team_name] = source["url"]
                    return source["url"]
        except:
            continue
    
    # Fallback cuối cùng
    fallback = f"https://ui-avatars.com/api/?name={requests.utils.quote(team_name)}&size=256&background=333&color=fff&bold=true"
    LOGO_CACHE[team_name] = fallback
    return fallback

# ==========================================
# HÀM PARSE THỜI GIAN (ĐÃ SỬA HOÀN TOÀN)
# ==========================================
def parse_url_to_info_buncha(url):
    """Parse URL để lấy thông tin trận đấu với thời gian chính xác"""
    try:
        # Ví dụ URL: /truc-tiep/metallurg-lipetsk-vs-fk-spartak-tambov-2026-05-09-2030
        match = re.search(r'/truc-tiep/([^/]+)', url)
        if match:
            slug = match.group(1)
            
            # Tìm pattern thời gian: YYYY-MM-DD-HHMM
            time_match = re.search(r'-(\d{4}-\d{2}-\d{2}-\d{4})$', slug)
            if time_match:
                time_str = time_match.group(1)
                # time_str = "2026-05-09-2030"
                
                # Tách các phần
                year = time_str[0:4]    # 2026
                month = time_str[5:7]   # 05
                day = time_str[8:10]    # 09
                hour = time_str[11:13]  # 20
                minute = time_str[13:15] # 30
                
                # Format đúng: "20:30 09/05/2026"
                thoi_gian = f"{hour}:{minute} {day}/{month}/{year}"
                
                # Lấy phần tên teams (bỏ phần thời gian)
                teams_part = slug[:slug.rfind('-' + time_str)]
            else:
                teams_part = slug
                thoi_gian = "Chưa có lịch"

            # Tách tên 2 đội
            if '-vs-' in teams_part:
                teams = teams_part.split('-vs-')
                doi_nha = teams[0].replace('-', ' ').title()
                doi_khach = teams[1].replace('-', ' ').title()
            else:
                doi_nha = teams_part.replace('-', ' ').title()
                doi_khach = "Unknown"
            
            return doi_nha.strip(), doi_khach.strip(), thoi_gian
            
    except Exception as e:
        print(f"  [!] Lỗi parse URL: {e}")
    
    return "Unknown", "Unknown", "Unknown"

# ==========================================
# HÀM BẮT LUỒNG M3U8 (ĐÃ SỬA)
# ==========================================
def capture_video_streams(page, match_url, match_title):
    """Bắt luồng video m3u8 từ trang"""
    video_streams = []
    
    def request_handler(request):
        url = request.url
        # Bắt TẤT CẢ các request liên quan đến video
        if any(ext in url.lower() for ext in ['.m3u8', '.ts', '.mp4', '.flv', 'live', 'stream', 'playlist']):
            if url not in video_streams:
                video_streams.append(url)
                print(f"         >> Phát hiện: {url[:100]}...")
    
    try:
        # Gắn listener TRƯỚC khi load trang
        page.on("request", request_handler)
        
        # Load trang trận đấu
        page.goto(match_url, timeout=30000, wait_until="domcontentloaded")
        page.wait_for_timeout(5000)
        
        # Click để kích hoạt video player
        try:
            # Click vào vùng video player
            page.click("video", timeout=3000)
        except:
            try:
                # Click vào iframe nếu có
                iframe = page.frame_locator("iframe").first
                iframe.locator("video").click(timeout=3000)
            except:
                # Click vào giữa màn hình
                viewport = page.viewport_size
                if viewport:
                    page.mouse.click(viewport['width']/2, viewport['height']/2)
        
        # Đợi video load
        page.wait_for_timeout(8000)
        
        # Thử tìm trong các iframe
        for frame in page.frames:
            try:
                frame_url = frame.url
                if any(ext in frame_url.lower() for ext in ['.m3u8', 'live', 'stream']):
                    video_streams.append(frame_url)
            except:
                pass
        
    except Exception as e:
        print(f"         [!] Lỗi: {e}")
    finally:
        page.remove_listener("request", request_handler)
    
    # Lọc stream chất lượng
    clean_streams = []
    for s in video_streams:
        # Loại bỏ stream quảng cáo
        if not any(ad in s.lower() for ad in ['ads', 'quangcao', 'advert', 'doubleclick', 'google']):
            clean_streams.append(s)
    
    # Ưu tiên stream m3u8 (HLS)
    m3u8_streams = [s for s in clean_streams if '.m3u8' in s.lower()]
    if m3u8_streams:
        return m3u8_streams[-1]  # Lấy stream cuối cùng (thường là chất lượng cao nhất)
    
    # Nếu không có m3u8, lấy stream khác
    if clean_streams:
        return clean_streams[-1]
    
    return None

# ==========================================
# HÀM TẠO M3U CHO MONPLAYER (ĐÃ SỬA)
# ==========================================
def create_m3u_for_monplayer(matches_data):
    """Tạo file M3U chuẩn cho MonPlayer"""
    
    m3u_lines = ['#EXTM3U']
    m3u_lines.append('')
    
    for idx, match in enumerate(matches_data, 1):
        # Lấy stream URL
        stream = match.get("luong_video", "")
        if not stream:
            stream = WAITING_VIDEO_URL
        
        # Logo URLs
        logo_home = match.get("logo_doi_nha", "")
        logo_away = match.get("logo_doi_khach", "")
        
        # Thời gian và trạng thái
        time_display = match.get("thoi_gian", "Chưa có lịch")
        status = match.get("trang_thai", "")
        
        # Icon trạng thái
        if "trực tiếp" in status.lower():
            status_icon = "🔴 LIVE"
        else:
            status_icon = "⏰"
        
        # Tên hiển thị đầy đủ
        display_name = f"{status_icon} {match['title']} | {time_display} | {status}"
        
        # Tạo EXTINF với metadata
        extinf = (
            f'#EXTINF:-1 '
            f'tvg-id="{idx}" '
            f'tvg-name="{match["title"]}" '
            f'tvg-logo="{logo_home}" '
            f'group-title="⚽ Bóng Đá Trực Tiếp" '
            f',{display_name}'
        )
        
        m3u_lines.append(extinf)
        m3u_lines.append(stream)
        m3u_lines.append('')
    
    return '\n'.join(m3u_lines)

# ==========================================
# HÀM CHÍNH SCRAPE (ĐÃ SỬA HOÀN TOÀN)
# ==========================================
def scrape_and_push():
    matches_data = []
    
    print("=" * 70)
    print(f"⏰ BẮT ĐẦU QUÉT: {datetime.datetime.now().strftime('%H:%M:%S %d/%m/%Y')}")
    print("=" * 70)
    
    with sync_playwright() as p:
        # Khởi tạo browser
        browser = p.chromium.launch(
            headless=True,
            args=[
                '--no-sandbox',
                '--disable-setuid-sandbox',
                '--disable-dev-shm-usage',
                '--disable-gpu',
                '--disable-web-security',
                '--disable-features=IsolateOrigins'
            ]
        )
        
        context = browser.new_context(
            viewport={'width': 1920, 'height': 1080},
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        )
        page = context.new_page()
        
        try:
            # ===== BƯỚC 1: LẤY DANH SÁCH TRẬN ĐẤU =====
            print("\n📋 BƯỚC 1: Lấy danh sách trận đấu...")
            page.goto(TARGET_SITE, timeout=60000)
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(5000)
            
            # Scroll để load đủ content
            for i in range(3):
                page.evaluate("window.scrollBy(0, 500)")
                page.wait_for_timeout(1000)
            
            # Tìm tất cả link trận đấu
            all_links = page.locator("a[href*='/truc-tiep/']").all()
            valid_matches = []
            seen = set()
            
            for link in all_links:
                href = link.get_attribute("href")
                if href and '-vs-' in href and href not in seen:
                    seen.add(href)
                    valid_matches.append(link)
            
            # Giới hạn số trận
            if LIMIT_MATCHES:
                valid_matches = valid_matches[:LIMIT_MATCHES]
            
            print(f"   ✓ Tìm thấy {len(valid_matches)} trận đấu")
            
            # ===== BƯỚC 2: PHÂN TÍCH TỪNG TRẬN =====
            print("\n📊 BƯỚC 2: Phân tích thông tin...")
            
            for i, el in enumerate(valid_matches):
                try:
                    # Lấy URL đầy đủ
                    match_url = el.get_attribute("href")
                    if match_url and not match_url.startswith("http"):
                        match_url = "https://bunchatv4.net" + match_url
                    
                    # Parse thông tin
                    doi_nha, doi_khach, thoi_gian = parse_url_to_info_buncha(match_url)
                    
                    # Kiểm tra trạng thái
                    element_text = el.inner_text()
                    is_live = any(kw in element_text.upper() for kw in ["LIVE", "TRỰC TIẾP", "HIỆP", "PHÚT"])
                    status = "Đang trực tiếp" if is_live else "Chưa đá"
                    
                    # Lấy logo từ trang web
                    logo_nha = get_team_logo(doi_nha)
                    logo_khach = get_team_logo(doi_khach)
                    
                    # Tìm logo trong HTML
                    images = el.locator("img").all()
                    for img in images:
                        src = img.get_attribute("src")
                        if src and ('team' in src.lower() or 'logo' in src.lower() or 'flag' in src.lower()):
                            if not src.startswith("http"):
                                src = "https:" + src if src.startswith("//") else "https://bunchatv4.net" + src
                            # Gán logo đầu tiên cho đội nhà, thứ 2 cho đội khách
                            if logo_nha == get_team_logo(doi_nha):  # Nếu chưa có logo từ web
                                logo_nha = src
                            elif logo_khach == get_team_logo(doi_khach):  # Nếu chưa có logo từ web
                                logo_khach = src
                                break
                    
                    matches_data.append({
                        "title": f"{doi_nha} vs {doi_khach}",
                        "trang_thai": status,
                        "thoi_gian": thoi_gian,
                        "logo_doi_nha": logo_nha,
                        "logo_doi_khach": logo_khach,
                        "link_xem": match_url,
                        "luong_video": ""
                    })
                    
                    print(f"   [{i+1:2d}/{len(valid_matches)}] {status}: {doi_nha} vs {doi_khach}")
                    print(f"         ⏰ {thoi_gian}")
                    
                except Exception as e:
                    print(f"   [!] Lỗi: {e}")
                    continue
            
            # ===== BƯỚC 3: BẮT LUỒNG VIDEO =====
            live_matches = [m for m in matches_data if "trực tiếp" in m["trang_thai"].lower()]
            print(f"\n🎥 BƯỚC 3: Bắt luồng video cho {len(live_matches)} trận live...")
            
            for idx, match in enumerate(live_matches):
                print(f"\n   [{idx+1}/{len(live_matches)}] {match['title']}")
                stream_url = capture_video_streams(page, match["link_xem"], match["title"])
                
                if stream_url:
                    match["luong_video"] = stream_url
                    print(f"         ✅ THÀNH CÔNG: {stream_url[:80]}...")
                else:
                    print(f"         ❌ KHÔNG có luồng")
                
                # Nghỉ giữa các lần bắt
                page.wait_for_timeout(2000)
            
        except Exception as e:
            print(f"\n❌ LỖI: {e}")
        finally:
            browser.close()
    
    # ===== TẠO FILE M3U =====
    if not matches_data:
        print("\n❌ KHÔNG có dữ liệu!")
        return
    
    print(f"\n📝 Tạo file M3U...")
    m3u_text = create_m3u_for_monplayer(matches_data)
    
    # In thống kê
    total_live = sum(1 for m in matches_data if "trực tiếp" in m["trang_thai"].lower())
    total_stream = sum(1 for m in matches_data if m["luong_video"])
    
    print(f"   ✓ Tổng số trận: {len(matches_data)}")
    print(f"   ✓ Đang live: {total_live}")
    print(f"   ✓ Có luồng: {total_stream}")
    
    # In mẫu 3 dòng đầu
    print("\n📄 MẪU M3U:")
    lines = m3u_text.split('\n')
    for line in lines[:12]:
        if line.strip():
            print(f"   {line[:120]}")
    print()
    
    # ===== PUSH LÊN GITHUB =====
    if GITHUB_TOKEN:
        try:
            g = Github(GITHUB_TOKEN)
            repo = g.get_repo(REPO_NAME)
            
            now = datetime.datetime.now()
            commit_msg = f"⚽ Update: {now.strftime('%H:%M %d/%m/%Y')} - {total_live} live, {total_stream} streams"
            
            try:
                contents = repo.get_contents(FILE_PATH)
                repo.update_file(contents.path, commit_msg, m3u_text, contents.sha)
                print(f"✅ ĐÃ CẬP NHẬT GitHub!")
            except:
                repo.create_file(FILE_PATH, commit_msg, m3u_text)
                print(f"✅ ĐÃ TẠO MỚI trên GitHub!")
                
        except Exception as e:
            print(f"❌ Lỗi GitHub: {e}")
            # Lưu local nếu không push được
            with open(FILE_PATH, 'w', encoding='utf-8') as f:
                f.write(m3u_text)
            print(f"   💾 Đã lưu local: {FILE_PATH}")
    else:
        # Lưu local
        with open(FILE_PATH, 'w', encoding='utf-8') as f:
            f.write(m3u_text)
        print(f"💾 Đã lưu local: {FILE_PATH}")
    
    print("\n" + "=" * 70)
    print("✅ HOÀN THÀNH!")
    print("=" * 70)

# ==========================================
# MAIN
# ==========================================
if __name__ == "__main__":
    scrape_and_push()
