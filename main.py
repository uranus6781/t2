import os
import datetime
import re
import time
import requests
import json
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
from github import Github

# ==========================================
# CẤU HÌNH
# ==========================================
TARGET_SITE = "https://bunchatv4.net/truc-tiep-bong-da-xoilac-tv"
GITHUB_TOKEN = os.getenv("GH_TOKEN")
REPO_NAME = os.getenv("GH_REPO", "Eternal161/dausoco")
FILE_PATH = "bongda.json"
WAITING_VIDEO_URL = "https://example.com/video-cho.mp4"
LIMIT_MATCHES = 35 # Tăng số lượng quét cho đủ danh sách của bạn

# Ép cứng múi giờ Việt Nam (UTC+7)
VN_TZ = datetime.timezone(datetime.timedelta(hours=7))

LOGO_CACHE = {}
_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

# ==========================================
# CHUẨN HÓA TÊN ĐỘI
# ==========================================
def normalize_team_name(raw: str) -> str:
    cleaned = re.sub(r'\bFc\b$', 'FC', raw)
    cleaned = re.sub(r'\bFootball Club\b', 'FC', cleaned)
    return cleaned.strip()

# ==========================================
# LẤY LOGO: BỘ 3 QUÉT ẢNH TỐI THƯỢNG
# ==========================================
def _logo_football_logos_cc(team_name: str) -> str | None:
    try:
        search_term = team_name.lower().replace(" ", "-")
        r = requests.get(f"https://football-logos.cc/search?q={search_term}", headers=_HEADERS, timeout=4)
        match = re.search(r'src="(https://football-logos.cc/logos/[^"]+\.png)"', r.text)
        if match: return match.group(1)
    except: pass
    return None

def _logo_espn(team_name: str) -> str | None:
    try:
        r = requests.get("https://site.api.espn.com/apis/site/v2/sports/soccer/all/teams", params={"search": team_name}, headers=_HEADERS, timeout=4)
        logos = r.json().get("sports", [])[0].get("leagues", [])[0].get("teams", [])[0].get("team", {}).get("logos", [])
        if logos: return logos[0].get("href")
    except: pass
    return None

def _logo_fotmob(team_name: str) -> str | None:
    try:
        r = requests.get("https://apigw.fotmob.com/searchapi/suggest", params={"term": team_name, "lang": "vi"}, headers=_HEADERS, timeout=4)
        match = re.search(r'"id"\s*:\s*"?(\d+)"?\s*,\s*"type"\s*:\s*"team"', r.text)
        if match: return f"https://images.fotmob.com/image_resources/logo/teamlogo/{match.group(1)}.png"
    except: pass
    return None

def _logo_fallback(team_name: str) -> str:
    initials = "".join(w[0].upper() for w in team_name.split()[:3] if w)
    return f"https://ui-avatars.com/api/?name={requests.utils.quote(initials)}&size=200&background=1565C0&color=ffffff&bold=true&format=png"

def get_team_logo(team_name: str) -> str:
    if not team_name or team_name == "Unknown": return _logo_fallback("?")
    search_name = normalize_team_name(team_name)
    if search_name in LOGO_CACHE: return LOGO_CACHE[search_name]

    logo = _logo_football_logos_cc(search_name) or _logo_espn(search_name) or _logo_fotmob(search_name) or _logo_fallback(team_name)
    LOGO_CACHE[search_name] = logo
    LOGO_CACHE[team_name] = logo
    print(f"      🏷  [{team_name}] → {logo[:55]}...")
    return logo

# ==========================================
# PARSE THÔNG TIN TRẬN
# ==========================================
def parse_url_to_info(url: str) -> tuple[str, str, str]:
    try:
        match = re.search(r'/truc-tiep/([^/?#]+)', url)
        if not match: return "Unknown", "Unknown", "Chưa có lịch"
        slug = match.group(1)
        time_match = re.search(r'(\d{4}-\d{2}-\d{2}-\d{4})$', slug)
        if time_match:
            t = time_match.group(1)
            thoi_gian = f"{t[0:2]}:{t[2:4]} {t[5:7]}/{t[8:10]}/{t[11:15]}"
            teams_slug = slug[: slug.rfind('-' + t)]
        else:
            thoi_gian, teams_slug = "Chưa có lịch", slug
        parts = teams_slug.split('-vs-', 1)
        return parts[0].replace('-', ' ').title().strip(), parts[1].replace('-', ' ').title().strip() if len(parts) > 1 else "Unknown", thoi_gian
    except Exception:
        return "Unknown", "Unknown", "Unknown"

# ==========================================
# BẮT LUỒNG M3U8 (VÁ LỖI XOILAC ANTI-ADBLOCK VÀ IFRAME)
# ==========================================
def capture_stream(context, match_url: str) -> str | None:
    page = context.new_page()
    streams = []

    def on_request(req):
        url = req.url.lower()
        if ".mp4" in url: return
        if ".m3u8" in url or ".flv" in url:
            # Lọc sơ bộ các link rác
            if "ad" in url and "live" not in url: return
            if req.url not in streams:
                streams.append(req.url)

    try:
        page.on("request", on_request)
        
        # BỎ CHẶN MẠNG! Dùng domcontentloaded để vào thẳng trang.
        page.goto(match_url, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(3000)

        # 1. Ép tất cả các frame (kể cả iframe) play video
        try:
            for frame in page.frames:
                frame.evaluate("""
                    document.querySelectorAll('video').forEach(v => {
                        v.muted = true; 
                        v.play().catch(() => {}); 
                    });
                """)
        except: pass

        # 2. Click bạo lực vào tâm màn hình và nhích lên một chút để trúng Iframe Player
        try:
            vp = page.viewport_size
            if vp:
                cx, cy = vp["width"] / 2, vp["height"] / 2
                page.mouse.click(cx, cy)
                page.wait_for_timeout(500)
                page.mouse.click(cx, cy - 100) 
        except: pass
        
        # 3. Quét tìm nút Play ẩn trong TẤT CẢ lớp Iframe
        try:
            for frame in page.frames:
                for sel in [".vjs-big-play-button", ".jw-icon-display", ".play-btn", "#player", ".play-wrapper"]:
                    btn = frame.locator(sel).first
                    if btn.is_visible(timeout=500):
                        btn.click()
                        break
        except: pass

        # Chờ tối đa 15s để lấy luồng
        deadline = time.time() + 15
        while time.time() < deadline:
            time.sleep(1)
            if streams:
                print(f"         ✅ Bắt được nguồn sau {(15 - (deadline - time.time())):.0f}s")
                break

    except PWTimeout: 
        print("         ⚠️  Trang tải quá lâu (Timeout)")
    except Exception as e: 
        print(f"         ❌ Lỗi bắt luồng: {e}")
    finally:
        page.close()
    
    if streams:
        live_streams = [s for s in streams if "live" in s.lower()]
        return (live_streams or streams)[-1]
    return None

# ==========================================
# TẠO JSON & PUSH LÊN GITHUB
# ==========================================
def create_json(matches_data: list) -> str:
    export = {
        "playlist_name": "Sáng TV",
        "last_updated": datetime.datetime.now(VN_TZ).strftime("%H:%M %d/%m/%Y"),
        "total_live": sum(1 for m in matches_data if m.get("is_live")),
        "total_streams": sum(1 for m in matches_data if m.get("stream_url") and m.get("stream_url") != WAITING_VIDEO_URL),
        "matches": matches_data,
    }
    return json.dumps(export, indent=2, ensure_ascii=False)

def push_to_github(content: str, live: int, streams: int) -> None:
    if not GITHUB_TOKEN:
        print("⚠️  Không có GH_TOKEN, lưu local.")
        with open(FILE_PATH, "w", encoding="utf-8") as f: f.write(content)
        return
    g = Github(GITHUB_TOKEN)
    repo = g.get_repo(REPO_NAME)
    msg = f"⚽ Cập nhật: {datetime.datetime.now(VN_TZ).strftime('%H:%M %d/%m/%Y')} — {live} live, {streams} streams"
    try:
        existing = repo.get_contents(FILE_PATH)
        repo.update_file(existing.path, msg, content, existing.sha)
        print(f"✅ Đã cập nhật GitHub: {FILE_PATH}")
    except:
        repo.create_file(FILE_PATH, msg, content)
        print(f"✅ Đã tạo mới trên GitHub: {FILE_PATH}")

# ==========================================
# HÀM CHÍNH
# ==========================================
def scrape_and_push():
    matches_data = []
    print("=" * 65)
    print(f"⏰ BẮT ĐẦU: {datetime.datetime.now(VN_TZ).strftime('%H:%M:%S %d/%m/%Y')}")
    print("=" * 65)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=[
            "--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage", "--disable-gpu",
            "--autoplay-policy=no-user-gesture-required", "--disable-web-security"
        ])
        ctx = browser.new_context(viewport={"width": 1920, "height": 1080}, user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
        main_page = ctx.new_page()

        print("\n📋 BƯỚC 1: Lấy danh sách trận...")
        try:
            main_page.goto(TARGET_SITE, timeout=60000)
            main_page.wait_for_load_state("networkidle", timeout=15000)
        except Exception as e: print(f"  ⚠️  Load chậm: {e}")

        for _ in range(3):
            main_page.evaluate("window.scrollBy(0, 900)")
            main_page.wait_for_timeout(800)

        seen_hrefs, valid = set(), []
        for link in main_page.locator("a[href*='/truc-tiep/']").all():
            href = link.get_attribute("href") or ""
            if "-vs-" in href and href not in seen_hrefs:
                seen_hrefs.add(href)
                valid.append(link)
        if LIMIT_MATCHES: valid = valid[:LIMIT_MATCHES]
        print(f"   ✓ Tìm thấy {len(valid)} trận")

        print("\n📊 BƯỚC 2: Phân tích trận & lấy logo...")
        for i, el in enumerate(valid):
            try:
                href = el.get_attribute("href") or ""
                if href and not href.startswith("http"): href = "/".join(TARGET_SITE.split("/")[:3]) + href
                doi_nha, doi_khach, thoi_gian = parse_url_to_info(href)

                # Bóc Logo trực tiếp từ giao diện trang
                logo_nha, logo_khach = "", ""
                try:
                    imgs = el.locator("img").all()
                    if len(imgs) >= 2:
                        src_nha = imgs[0].get_attribute("data-src") or imgs[0].get_attribute("src")
                        src_khach = imgs[1].get_attribute("data-src") or imgs[1].get_attribute("src")
                        if src_nha and ".gif" not in src_nha: logo_nha = src_nha if src_nha.startswith("http") else f"https://bunchatv4.net{src_nha}"
                        if src_khach and ".gif" not in src_khach: logo_khach = src_khach if src_khach.startswith("http") else f"https://bunchatv4.net{src_khach}"
                except: pass
                
                # Gọi API dự phòng
                if not logo_nha: logo_nha = get_team_logo(doi_nha)
                if not logo_khach: logo_khach = get_team_logo(doi_khach)

                # So sánh thời gian thực: Kích hoạt Live từ -10 phút đến +120 phút
                is_live, status = False, "Chưa đá ⏳"
                try:
                    match_time = datetime.datetime.strptime(thoi_gian, "%H:%M %d/%m/%Y").replace(tzinfo=VN_TZ)
                    diff_minutes = (datetime.datetime.now(VN_TZ) - match_time).total_seconds() / 60
                    if -10 <= diff_minutes <= 120:  
                        is_live, status = True, "Đang trực tiếp 🔴"
                    elif diff_minutes > 120:
                        status = "Đã kết thúc 🏁"
                except:
                    if any(kw in el.inner_text().upper() for kw in ["LIVE", "HIỆP", "PHÚT"]):
                        is_live, status = True, "Đang trực tiếp 🔴"

                matches_data.append({
                    "id": str(i + 1), "title": f"{doi_nha} vs {doi_khach}",
                    "doi_nha": doi_nha, "doi_khach": doi_khach,
                    "trang_thai": status, "is_live": is_live, "thoi_gian": thoi_gian,
                    "logo_nha": logo_nha, "logo_khach": logo_khach,
                    "link_xem": href, "stream_url": WAITING_VIDEO_URL,
                })
                print(f"   [{i+1:2d}/{len(valid)}] {'🔴' if is_live else '⚪'} {doi_nha} vs {doi_khach}  |  {thoi_gian}")
            except Exception as e: print(f"   [!] Lỗi trận {i+1}: {e}")
            
        main_page.close() 

        live_matches = [m for m in matches_data if m["is_live"]]
        print(f"\n🎥 BƯỚC 3: Bắt luồng {len(live_matches)} trận live...")
        for idx, match in enumerate(live_matches):
            print(f"\n   [{idx+1}/{len(live_matches)}] {match['title']}")
            stream = capture_stream(ctx, match["link_xem"])
            if stream:
                match["stream_url"] = stream
                print(f"         📡 {stream[:70]}...")
            else: print("         ❌ Không tìm được luồng")

        browser.close()

    if not matches_data:
        print("\n❌ Không có dữ liệu!")
        return

    live_cnt = sum(1 for m in matches_data if m["is_live"])
    stream_cnt = sum(1 for m in matches_data if m["stream_url"] != WAITING_VIDEO_URL)
    push_to_github(create_json(matches_data), live_cnt, stream_cnt)
    
    print(f"\n{'='*65}\n✅ XONG — {len(matches_data)} trận | {live_cnt} live | {stream_cnt} có luồng\n{'='*65}")

if __name__ == "__main__":
    scrape_and_push()
