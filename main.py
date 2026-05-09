import os
import re
import time
import json
import datetime
import requests

from github import Github
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

# ==========================================
# CẤU HÌNH
# ==========================================
TARGET_SITE = "https://bunchatv4.net/truc-tiep-bong-da-xoilac-tv"
GITHUB_TOKEN = os.getenv("GH_TOKEN")
REPO_NAME = os.getenv("GH_REPO", "Eternal161/dausoco")
FILE_PATH = "bongda.json"
WAITING_VIDEO_URL = "https://example.com/video-cho.mp4"

LIMIT_MATCHES = 10
MAX_STREAM_WAIT = 25
STREAM_POLL_INTERVAL = 1

VN_TZ = datetime.timezone(datetime.timedelta(hours=7))

LOGO_CACHE = {}
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7",
}

# ==========================================
# CHUẨN HÓA TÊN ĐỘI
# ==========================================
def normalize_team_name(raw: str) -> str:
    cleaned = re.sub(r'\bFc\b$', 'FC', raw)
    cleaned = re.sub(r'\bFootball Club\b', 'FC', cleaned)
    return cleaned.strip()

# ==========================================
# LẤY LOGO XỊN
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
# PARSE URL → THÔNG TIN TRẬN
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
# BẮT LUỒNG M3U8 (CƠ CHẾ NGUYÊN THỦY CỦA BẠN)
# ==========================================
# VÁ LỖI ADELAIDE: Dùng "/ad/" và "/ads/" thay vì "ad"
_AD_KEYWORDS = ["/ad/", "/ads/", "/vast/", "advertisement", "doubleclick", "googlesyndication", "quangcao", "preroll", "midroll"]
_SKIP_SELECTORS = [
    ".skip-ad-btn", ".vast-skip-button", ".skip-button", "[class*='skip']", "[id*='skip']",
    "xpath=//*[contains(translate(text(),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'bỏ qua')]",
    "xpath=//*[contains(translate(text(),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'skip ad')]"
]

def _trigger_player(page) -> None:
    try:
        page.evaluate("""
            document.querySelectorAll('video').forEach(v => {
                v.muted = true; v.play().catch(() => {});
            });
        """)
    except: pass

    for selector in [".vjs-big-play-button", ".jw-icon-display", ".play-btn", ".play-wrapper", "[class*='play']"]:
        try:
            btn = page.locator(selector).first
            if btn.is_visible(timeout=800):
                btn.click(timeout=800)
                break
        except: pass

    try:
        vp = page.viewport_size
        if vp: page.mouse.click(vp["width"] / 2, vp["height"] / 2)
    except: pass

def _try_skip_ad(page) -> bool:
    for sel in _SKIP_SELECTORS:
        try:
            btn = page.locator(sel).first
            if btn.is_visible(timeout=1500):
                btn.click(timeout=1500)
                page.wait_for_timeout(1500)
                return True
        except: pass
    return False

def capture_stream(context, match_url: str) -> str | None:
    page = context.new_page()
    streams = []
    ad_streams = set()

    def on_request(req):
        url = req.url.lower()
        if ".mp4" in url: return
        if ".m3u8" in url or ".flv" in url:
            is_ad = any(kw in url for kw in _AD_KEYWORDS)
            if is_ad: 
                ad_streams.add(req.url)
            elif req.url not in streams: 
                streams.append(req.url)

    try:
        page.on("request", on_request)
        page.goto(match_url, timeout=60_000, wait_until="domcontentloaded")
        page.wait_for_timeout(2000)
        
        _trigger_player(page)

        deadline = time.time() + MAX_STREAM_WAIT
        skip_attempted = False

        while time.time() < deadline:
            time.sleep(STREAM_POLL_INTERVAL)
            if not skip_attempted and ad_streams:
                skip_attempted = True
                if _try_skip_ad(page):
                    print("         🔪 Đã skip quảng cáo, xóa luồng ads...")
                    streams.clear()
                    ad_streams.clear()
            if streams:
                print(f"         ✅ Có luồng sau {MAX_STREAM_WAIT - (deadline - time.time()):.0f}s")
                break

    except PWTimeout: 
        print("         ⚠️  Timeout trang")
    except Exception as e: 
        print(f"         ❌ Lỗi: {e}")
    finally:
        try: page.remove_listener("request", on_request)
        except: pass
        page.close()

    live_streams = [s for s in streams if "live" in s.lower()]
    return (live_streams or streams or [None])[-1]

# ==========================================
# TẠO JSON & PUSH LÊN GITHUB
# ==========================================
def create_json(matches_data: list) -> str:
    live_count = sum(1 for m in matches_data if m.get("is_live"))
    stream_count = sum(1 for m in matches_data if m.get("stream_url") and m.get("stream_url") != WAITING_VIDEO_URL)
    
    export = {
        "playlist_name": "Sáng TV",
        "last_updated": datetime.datetime.now(VN_TZ).strftime("%H:%M %d/%m/%Y"),
        "total_live": live_count,
        "total_streams": stream_count,
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
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage", "--disable-gpu",
                "--autoplay-policy=no-user-gesture-required", "--disable-web-security",
                "--disable-features=IsolateOrigins,site-per-process",
            ]
        )
        ctx = browser.new_context(
            viewport={"width": 1920, "height": 1080}, 
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        )
        page = ctx.new_page()

        print("\n📋 BƯỚC 1: Lấy danh sách trận...")
        try:
            page.goto(TARGET_SITE, timeout=60000)
            page.wait_for_load_state("networkidle", timeout=15000)
        except Exception as e: print(f"  ⚠️  Load chậm: {e}")

        for _ in range(3):
            page.evaluate("window.scrollBy(0, 900)")
            page.wait_for_timeout(800)

        seen_hrefs, valid = set(), []
        for link in page.locator("a[href*='/truc-tiep/']").all():
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

                # Cào Logo
                logo_nha, logo_khach = "", ""
                try:
                    imgs = el.locator("img").all()
                    if len(imgs) >= 2:
                        src_nha = imgs[0].get_attribute("data-src") or imgs[0].get_attribute("src")
                        src_khach = imgs[1].get_attribute("data-src") or imgs[1].get_attribute("src")
                        if src_nha and ".gif" not in src_nha: logo_nha = src_nha if src_nha.startswith("http") else f"https://bunchatv4.net{src_nha}"
                        if src_khach and ".gif" not in src_khach: logo_khach = src_khach if src_khach.startswith("http") else f"https://bunchatv4.net{src_khach}"
                except: pass
                
                if not logo_nha: logo_nha = get_team_logo(doi_nha)
                if not logo_khach: logo_khach = get_team_logo(doi_khach)

                # Thuật toán LIVE chuẩn (-10 phút)
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
            
        page.close() 

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
