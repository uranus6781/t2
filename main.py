import os
import re
import time
import json
import datetime
import requests

from github import Github
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
from playwright_stealth import Stealth

# ==========================================
# CONFIG
# ==========================================
TARGET_SITE = "https://bunchatv4.net/truc-tiep-bong-da-xoilac-tv"
GITHUB_TOKEN = os.getenv("GH_TOKEN")
REPO_NAME = os.getenv("GH_REPO", "Eternal161/dausoco")
FILE_PATH = "bongda.json"
WAITING_VIDEO_URL = "https://example.com/video-cho.mp4"
LIMIT_MATCHES = 10
VN_TZ = datetime.timezone(datetime.timedelta(hours=7))

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7",
}

LOGO_CACHE = {}

# ==========================================
# CHUẨN HÓA TÊN ĐỘI
# ==========================================
_TEAM_NAME_MAP = {
    "Clb Thanh Hoa": "Thanh Hoa FC", "Tt Hanoi": "Hanoi FC", "Ttbd Phu Dong": "Phu Dong FC", 
    "Xm Hai Phong Fc": "Hai Phong FC", "Slna": "SLNA FC", "Becamex Binh Duong": "Becamex Binh Duong FC",
    "Hoang Anh Gia Lai": "HAGL FC", "Hagl": "HAGL FC", "Viettel Fc": "Viettel FC", 
    "Nam Dinh Fc": "Nam Dinh FC", "Khanh Hoa Fc": "Khanh Hoa FC", "Cong An Ha Noi": "Cong An Ha Noi FC",
    "Mito Hollyhock": "Mito HollyHock", "Cerezo Osaka": "Cerezo Osaka", "Urawa Red Diamonds": "Urawa Red Diamonds",
    "Jeju Sk Fc": "Jeju United FC", "Football Club Seoul": "FC Seoul", "Pohang Steelers": "Pohang Steelers",
    "Man Utd": "Manchester United FC", "Man City": "Manchester City FC", "Tottenham": "Tottenham Hotspur FC", 
    "Paris Saint Germain": "Paris Saint-Germain FC", "Psg": "Paris Saint-Germain FC",
}

def normalize_team_name(raw: str) -> str:
    if raw in _TEAM_NAME_MAP: return _TEAM_NAME_MAP[raw]
    cleaned = re.sub(r'\bFc\b$', 'FC', raw)
    cleaned = re.sub(r'\bFootball Club\b', 'FC', cleaned)
    return cleaned.strip()

# ==========================================
# LẤY LOGO XỊN (Khôi phục từ bản trước)
# ==========================================
def _logo_football_logos_cc(team_name: str):
    try:
        r = requests.get(f"https://football-logos.cc/search?q={team_name.lower().replace(' ', '-')}", headers=_HEADERS, timeout=4)
        match = re.search(r'src="(https://football-logos.cc/logos/[^"]+\.png)"', r.text)
        if match: return match.group(1)
    except: pass
    return None

def _logo_espn(team_name: str):
    try:
        r = requests.get("https://site.api.espn.com/apis/site/v2/sports/soccer/all/teams", params={"search": team_name}, headers=_HEADERS, timeout=4)
        logos = r.json().get("sports", [])[0].get("leagues", [])[0].get("teams", [])[0].get("team", {}).get("logos", [])
        if logos: return logos[0].get("href")
    except: pass
    return None

def _logo_fotmob(team_name: str):
    try:
        r = requests.get("https://apigw.fotmob.com/searchapi/suggest", params={"term": team_name, "lang": "vi"}, headers=_HEADERS, timeout=4)
        teams = r.json().get("suggest", {}).get("team", [])
        if teams and teams[0].get("id"): return f"https://images.fotmob.com/image_resources/logo/teamlogo/{teams[0]['id']}.png"
    except: pass
    return None

def _logo_fallback(team_name: str):
    initials = "".join(w[0].upper() for w in team_name.split()[:3] if w)
    return f"https://ui-avatars.com/api/?name={requests.utils.quote(initials)}&size=200&background=1565C0&color=ffffff&bold=true&format=png"

def get_team_logo(team_name: str) -> str:
    if not team_name or team_name == "Unknown": return _logo_fallback("?")
    search_name = normalize_team_name(team_name)
    if search_name in LOGO_CACHE: return LOGO_CACHE[search_name]

    logo = _logo_football_logos_cc(search_name) or _logo_espn(search_name) or _logo_fotmob(search_name) or _logo_fallback(team_name)
    LOGO_CACHE[search_name] = logo
    LOGO_CACHE[team_name] = logo
    return logo

# ==========================================
# PARSE URL
# ==========================================
def parse_url_to_info(url: str):
    try:
        match = re.search(r"/truc-tiep/([^/?#]+)", url)
        if not match: return "Unknown", "Unknown", "Chưa có lịch"
        slug = match.group(1)
        time_match = re.search(r"(\d{4}-\d{2}-\d{2}-\d{4})$", slug) # Fix regex của bạn bị thiếu cụm -dd
        if time_match:
            t = time_match.group(1)
            thoi_gian = f"{t[0:2]}:{t[2:4]} {t[5:7]}/{t[8:10]}/{t[11:15]}"
            teams_slug = slug[: slug.rfind("-" + t)]
        else:
            thoi_gian, teams_slug = "Chưa có lịch", slug
        parts = teams_slug.split("-vs-", 1)
        return parts[0].replace("-", " ").title().strip(), parts[1].replace("-", " ").title().strip() if len(parts) > 1 else "Unknown", thoi_gian
    except:
        return "Unknown", "Unknown", "Unknown"

# ==========================================
# CAPTURE STREAM (Giữ nguyên siêu kỹ thuật của Dậu)
# ==========================================
def capture_stream(context, match_url):

    page = context.new_page()

    Stealth().apply_stealth_sync(page)

    streams = set()

    def handle_url(url):
        u = url.lower()
        if ".mp4" in u: return
        if ".m3u8" in u or ".flv" in u:
            if "ads" in u: return
            streams.add(url)
            print(f"🎯 FOUND STREAM: {url[:60]}...  Untitled1:126 - main.py:129")

    page.on("request", lambda r: handle_url(r.url))
    page.on("response", lambda r: handle_url(r.url))

    try:
        # Anti bot
        page.add_init_script("Object.defineProperty(navigator, 'webdriver', { get: () => undefined });")
        # Hook fetch + xhr
        page.add_init_script("""
        (() => {
            const origFetch = window.fetch;
            window.fetch = async (...args) => {
                const url = args[0];
                if (typeof url === 'string' && (url.includes('.m3u8') || url.includes('.flv'))) {
                    console.log('FETCH STREAM:', url);
                }
                return origFetch(...args);
            };
            const origOpen = XMLHttpRequest.prototype.open;
            XMLHttpRequest.prototype.open = function(method, url) {
                if (url.includes('.m3u8') || url.includes('.flv')) console.log('XHR STREAM:', url);
                return origOpen.apply(this, arguments);
            };
        })();
        """)

        page.goto(match_url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(3000)

        # Remove overlays (Lớp phủ chặn click)
        try:
            page.evaluate("""
            document.querySelectorAll('*').forEach(el => {
                const style = window.getComputedStyle(el);
                if (style.position === 'fixed' && parseInt(style.zIndex) > 999) el.remove();
            });
            """)
        except: pass

        # Click center screen 2 lần phá quảng cáo
        try:
            vp = page.viewport_size
            if vp:
                cx, cy = vp["width"] // 2, vp["height"] // 2
                page.mouse.click(cx, cy)
                page.wait_for_timeout(1000)
                page.mouse.click(cx, cy)
        except: pass

        # Ép Iframe Play
        for frame in page.frames:
            try: frame.click("video", timeout=1000)
            except: pass
            try: frame.click("button", timeout=1000)
            except: pass
            try:
                frame.evaluate("""
                document.querySelectorAll('video').forEach(v => {
                    v.muted = true;
                    const p = v.play();
                    if (p !== undefined) p.catch(()=>{});
                });
                """)
            except: pass

        # Đợi luồng chui ra
        try:
            page.wait_for_response(lambda r: ".m3u8" in r.url.lower() or ".flv" in r.url.lower(), timeout=12000)
        except: pass

    except PWTimeout: print("         ⚠️ TIMEOUT")
    except Exception as e: print("         ❌ STREAM ERROR:", e)
    finally: page.close()

    if streams:
        streams = sorted(streams)
        live_streams = [s for s in streams if "live" in s.lower()]
        best = live_streams[-1] if live_streams else streams[-1]
        return best
    return None

# ==========================================
# JSON & GITHUB
# ==========================================
def create_json(matches_data):
    export = {
        "playlist_name": "Sáng TV",
        "last_updated": datetime.datetime.now(VN_TZ).strftime("%H:%M %d/%m/%Y"),
        "total_live": sum(1 for m in matches_data if m.get("is_live")),
        "total_streams": sum(1 for m in matches_data if m.get("stream_url") and m.get("stream_url") != WAITING_VIDEO_URL),
        "matches": matches_data,
    }
    return json.dumps(export, indent=2, ensure_ascii=False)

def push_to_github(content, live, streams):
    if not GITHUB_TOKEN:
        print("⚠️ NO GH_TOKEN")
        with open(FILE_PATH, "w", encoding="utf-8") as f: f.write(content)
        return
    g, repo = Github(GITHUB_TOKEN), Github(GITHUB_TOKEN).get_repo(REPO_NAME)
    msg = f"⚽ Update {datetime.datetime.now(VN_TZ).strftime('%H:%M %d/%m/%Y')}"
    try:
        existing = repo.get_contents(FILE_PATH)
        repo.update_file(existing.path, msg, content, existing.sha)
        print("✅ UPDATED GITHUB")
    except:
        repo.create_file(FILE_PATH, msg, content)
        print("✅ CREATED FILE")

# ==========================================
# MAIN
# ==========================================
def scrape_and_push():
    matches_data = []
    print("=" * 70)
    print(datetime.datetime.now(VN_TZ).strftime("START %H:%M:%S %d/%m/%Y"))
    print("=" * 70)

    with sync_playwright() as p:
        # BẮT BUỘC HEADLESS=TRUE ĐỂ CHẠY ĐƯỢC TRÊN GITHUB ACTIONS
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage",
                "--autoplay-policy=no-user-gesture-required",
            ]
        )
        ctx = browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent=_HEADERS["User-Agent"],
            java_script_enabled=True, bypass_csp=True, ignore_https_errors=True,
        )
        page = ctx.new_page()
        Stealth().apply_stealth_sync(page)

        print("\n📋 LOAD MATCH LIST")
        try:
            page.goto(TARGET_SITE, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(5000)
        except: pass

        for _ in range(5):
            page.mouse.wheel(0, 3000)
            page.wait_for_timeout(1000)

        seen, valid = set(), []
        for link in page.locator("a[href*='/truc-tiep/']").all():
            href = link.get_attribute("href") or ""
            if "-vs-" in href and href not in seen:
                seen.add(href)
                valid.append(link)

        if LIMIT_MATCHES: valid = valid[:LIMIT_MATCHES]
        print(f"✅ FOUND {len(valid)} MATCHES")

        print("\n📊 PHÂN TÍCH TRẬN & LOGO...")
        for i, el in enumerate(valid):
            try:
                href = el.get_attribute("href") or ""
                if href and not href.startswith("http"): href = "/".join(TARGET_SITE.split("/")[:3]) + href
                doi_nha, doi_khach, thoi_gian = parse_url_to_info(href)

                # Khôi phục kiểm tra giờ thực tế thay vì cho Live tất cả
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

                # Quét logo từ giao diện trước
                logo_nha, logo_khach = "", ""
                try:
                    imgs = el.locator("img").all()
                    if len(imgs) >= 2:
                        src_nha = imgs[0].get_attribute("data-lazy-src") or imgs[0].get_attribute("src")
                        src_khach = imgs[1].get_attribute("data-lazy-src") or imgs[1].get_attribute("src")
                        if src_nha and ".gif" not in src_nha: logo_nha = src_nha if src_nha.startswith("http") else f"https://bunchatv4.net{src_nha}"
                        if src_khach and ".gif" not in src_khach: logo_khach = src_khach if src_khach.startswith("http") else f"https://bunchatv4.net{src_khach}"
                except: pass
                
                if not logo_nha: logo_nha = get_team_logo(doi_nha)
                if not logo_khach: logo_khach = get_team_logo(doi_khach)

                matches_data.append({
                    "id": str(i + 1), "title": f"{doi_nha} vs {doi_khach}",
                    "doi_nha": doi_nha, "doi_khach": doi_khach,
                    "trang_thai": status, "is_live": is_live, "thoi_gian": thoi_gian,
                    "logo_nha": logo_nha, "logo_khach": logo_khach,
                    "stream_url": WAITING_VIDEO_URL, "link_xem": href,
                })
                print(f"   [{i+1}] {'🔴' if is_live else '⚪'} {doi_nha} vs {doi_khach}")
            except Exception as e: print("❌", e)

        page.close()

        live_matches = [m for m in matches_data if m["is_live"]]
        print(f"\n🎥 CAPTURE {len(live_matches)} STREAMS")

        for idx, match in enumerate(live_matches):
            print(f"\n[{idx+1}/{len(live_matches)}] {match['title']}")
            stream = capture_stream(ctx, match["link_xem"])
            if stream:
                match["stream_url"] = stream
            else:
                print("         ❌ NO STREAM")

        browser.close()

    if not matches_data:
        print("❌ NO DATA")
        return

    live_cnt = sum(1 for m in matches_data if m["is_live"])
    stream_cnt = sum(1 for m in matches_data if m["stream_url"] != WAITING_VIDEO_URL)
    
    push_to_github(create_json(matches_data), live_cnt, stream_cnt)

    print("\n" + "=" * 70)
    print(f"✅ DONE | {len(matches_data)} matches | {live_cnt} live | {stream_cnt} streams")
    print("=" * 70)

if __name__ == "__main__":
    scrape_and_push()
