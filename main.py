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
LIMIT_MATCHES = 5 # Giới hạn số trận để test/chạy nhanh

LOGO_CACHE = {}

# ==========================================
# HÀM LẤY LOGO (SOFASCORE + NÉN SIZE 200x200)
# ==========================================
def get_team_logo(team_name):
    """Tìm logo chuẩn HD từ SofaScore và nén size tự động bằng Image Proxy"""
    if not team_name or team_name == "Unknown" or len(team_name) < 3:
        return "https://cdn.sofascore.app/api/v1/team/placeholder/image"
    
    if team_name in LOGO_CACHE:
        return LOGO_CACHE[team_name]
    
    try:
        search_url = f"https://api.sofascore.app/api/v1/search/all?q={requests.utils.quote(team_name)}"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        response = requests.get(search_url, headers=headers, timeout=5)
        data = response.json()
        
        if data and "results" in data:
            for item in data["results"]:
                if item.get("type") == "team":
                    team_id = item["entity"]["id"]
                    raw_logo = f"https://api.sofascore.app/api/v1/team/{team_id}/image"
                    # Ép size 200x200 cho MonPlayer chạy mượt
                    optimized_logo = f"https://wsrv.nl/?url={raw_logo}&w=200&h=200&fit=contain&output=png"
                    
                    LOGO_CACHE[team_name] = optimized_logo
                    return optimized_logo
    except:
        pass
    
    # Fallback dự phòng: Tạo ảnh chữ cái
    fallback = f"https://ui-avatars.com/api/?name={requests.utils.quote(team_name)}&size=200&background=1a73e8&color=fff&bold=true&format=png"
    LOGO_CACHE[team_name] = fallback
    return fallback

# ==========================================
# HÀM PARSE THỜI GIAN
# ==========================================
def parse_url_to_info_buncha(url):
    """Parse URL để lấy thời gian CHUẨN"""
    try:
        match = re.search(r'/truc-tiep/([^/]+)', url)
        if match:
            slug = match.group(1)
            
            # Format: YYYY-MM-DD-HHMM (VD: 2026-05-09-2030)
            time_match = re.search(r'-(\d{4}-\d{2}-\d{2}-\d{4})$', slug)
            if time_match:
                time_str = time_match.group(1)
                
                year = time_str[0:4]
                month = time_str[5:7]
                day = time_str[8:10]
                hour = time_str[11:13]
                minute = time_str[13:15]
                
                thoi_gian = f"{hour}:{minute} {day}/{month}/{year}"
                teams_part = slug[:slug.rfind('-' + time_str)]
            else:
                teams_part = slug
                thoi_gian = "Chưa có lịch"

            teams = teams_part.split('-vs-')
            doi_nha = teams[0].replace('-', ' ').title()
            doi_khach = teams[1].replace('-', ' ').title() if len(teams) > 1 else "Unknown"
            
            return doi_nha.strip(), doi_khach.strip(), thoi_gian
            
    except Exception as e:
        print(f"  [!] Lỗi parse URL: {e}")
    
    return "Unknown", "Unknown", "Unknown"

# ==========================================
# HÀM BẮT LUỒNG & CHÉM QUẢNG CÁO
# ==========================================
def capture_video_streams(page, match_url):
    """Vào phòng, click player, chém quảng cáo và lấy M3U8"""
    video_streams = []
    
    def request_handler(request):
        if ".m3u8" in request.url or ".flv" in request.url:
            video_streams.append(request.url)

    try:
        page.on("request", request_handler)
        page.goto(match_url, timeout=60000)
        page.wait_for_timeout(3000)
        
        viewport_size = page.viewport_size
        if viewport_size:
            page.mouse.click(viewport_size['width']/2, viewport_size['height']/2)
            
            # Đứng canh chém quảng cáo 10 giây
            try:
                skip_btn = page.locator("xpath=//*[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'bỏ qua') or contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'skip')]").first
                skip_btn.wait_for(state="visible", timeout=10000)
                skip_btn.click()
                video_streams.clear() # Xóa sạch link rác của quảng cáo
                page.wait_for_timeout(2000) 
            except:
                pass
                
        page.wait_for_timeout(8000)
    except Exception as e:
        print(f"         ❌ LỖI VÀO PHÒNG: {e}")
    finally:
        page.remove_listener("request", request_handler)
        
    clean_streams = [s for s in video_streams if not any(ad in s.lower() for ad in ["ads", "quangcao", "advertisement", "doubleclick"])]
    
    if clean_streams:
        return clean_streams[-1]
    return None

# ==========================================
# HÀM TẠO M3U CHO MONPLAYER
# ==========================================
def create_m3u_for_monplayer(matches_data):
    """Tạo file M3U chuẩn có Emoji cho MonPlayer"""
    m3u_lines = ['#EXTM3U']
    m3u_lines.append('#PLAYLIST:Bóng Đá Trực Tiếp - Bún Chả TV')
    m3u_lines.append('')
    
    for idx, match in enumerate(matches_data, 1):
        stream = match.get("luong_video", "")
        if not stream:
            stream = WAITING_VIDEO_URL
        
        logo_home = match.get("logo_doi_nha", "")
        time_display = match.get("thoi_gian", "Chưa có lịch")
        status = match.get("trang_thai", "")
        
        if "trực tiếp" in status.lower():
            status_icon = "🔴 LIVE"
        elif "chưa đá" in status.lower():
            status_icon = "⏰"
        else:
            status_icon = "⚽"
        
        display_name = f"{status_icon} {match['title']} | {time_display} | {status}"
        
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
    
    print("=" * 70)
    print(f"⏰ BẮT ĐẦU QUÉT: {datetime.datetime.now().strftime('%H:%M:%S %d/%m/%Y')}")
    print("=" * 70)
    
    with sync_playwright() as p:
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
            page.wait_for_timeout(3000)
            
            for _ in range(3):
                page.evaluate("window.scrollBy(0, 800)")
                page.wait_for_timeout(1000)
            
            all_links = page.locator("a[href*='/truc-tiep/']").all()
            valid_matches = []
            seen = set()
            
            for link in all_links:
                href = link.get_attribute("href")
                if href and '-vs-' in href and href not in seen:
                    seen.add(href)
                    valid_matches.append(link)
            
            if LIMIT_MATCHES:
                valid_matches = valid_matches[:LIMIT_MATCHES]
            
            print(f"   ✓ Tìm thấy {len(valid_matches)} trận đấu")
            
            # ===== BƯỚC 2: PHÂN TÍCH TỪNG TRẬN =====
            print("\n📊 BƯỚC 2: Phân tích thông tin...")
            
            for i, el in enumerate(valid_matches):
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
                    
                    # LOGO: Mở rộng điều kiện bắt logo web
                    images = el.locator("img").all()
                    web_logos = []
                    for img in images:
                        src = img.get_attribute("src")
                        if src and "avatar" not in src.lower() and "icon" not in src.lower():
                            if not src.startswith("http"):
                                domain = "/".join(TARGET_SITE.split("/")[:3])
                                src = domain + src
                            web_logos.append(src)
                            if len(web_logos) >= 2:
                                break
                    
                    if len(web_logos) >= 2:
                        logo_nha = web_logos[0]
                        logo_khach = web_logos[1]
                    elif len(web_logos) == 1:
                        logo_nha = web_logos[0]
                        logo_khach = get_team_logo(doi_khach)
                    else:
                        logo_nha = get_team_logo(doi_nha)
                        logo_khach = get_team_logo(doi_khach)
                    
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
                    
                except Exception as e:
                    print(f"   [!] Lỗi xử lý trận {i+1}: {e}")
                    continue
            
            # ===== BƯỚC 3: BẮT LUỒNG VIDEO =====
            live_matches = [m for m in matches_data if "trực tiếp" in m["trang_thai"].lower()]
            print(f"\n🎥 BƯỚC 3: Bắt luồng video cho {len(live_matches)} trận live...")
            
            for idx, match in enumerate(live_matches):
                print(f"\n   [{idx+1}/{len(live_matches)}] Đang dò luồng: {match['title']}")
                
                # Gọi hàm bắt luồng và chém quảng cáo
                stream_url = capture_video_streams(page, match["link_xem"])
                
                if stream_url:
                    match["luong_video"] = stream_url
                    print(f"         ✅ THÀNH CÔNG LẤY LUỒNG CHÍNH!")
                else:
                    print(f"         ❌ KHÔNG có luồng")
                
                page.wait_for_timeout(1000)
            
        except Exception as e:
            print(f"\n❌ LỖI HỆ THỐNG: {e}")
        finally:
            browser.close()
    
    # ===== TẠO FILE M3U VÀ PUSH =====
    if not matches_data:
        print("\n❌ KHÔNG có dữ liệu!")
        return
    
    print(f"\n📝 Tạo file M3U...")
    m3u_text = create_m3u_for_monplayer(matches_data)
    
    if GITHUB_TOKEN:
        try:
            g = Github(GITHUB_TOKEN)
            repo = g.get_repo(REPO_NAME)
            
            now = datetime.datetime.now()
            total_live = sum(1 for m in matches_data if "trực tiếp" in m["trang_thai"].lower())
            total_stream = sum(1 for m in matches_data if m["luong_video"])
            
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
            with open(FILE_PATH, 'w', encoding='utf-8') as f:
                f.write(m3u_text)
            print(f"   💾 Đã lưu local: {FILE_PATH}")
    else:
        with open(FILE_PATH, 'w', encoding='utf-8') as f:
            f.write(m3u_text)
        print(f"💾 Đã lưu local: {FILE_PATH}")
    
    print("\n" + "=" * 70)
    print("✅ HOÀN THÀNH!")
    print("=" * 70)

if __name__ == "__main__":
    scrape_and_push()
