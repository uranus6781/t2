import os
import re
import time
import json
import datetime
import requests

from github import Github
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
from playwright_stealth import Stealth

# =========================================================
# CONFIG ĐA KÊNH
# =========================================================

CHANNELS = [
    {
        "id": "buncha",
        "name": "Bún Chả TV",
        "url": "https://bunchatv4.net/truc-tiep-bong-da-xoilac-tv",
        "base_url": "https://bunchatv4.net"
    },
    {
        "id": "hoiquan",
        "name": "Hội Quán TV",
        "url": "https://sv2.hoiquan3.live/lich-thi-dau/bong-da",
        "base_url": "https://sv2.hoiquan3.live"
    }
]

FILE_PATH = "bongda.json"
WAITING_VIDEO_URL = "https://example.com/waiting.mp4"
LIMIT_MATCHES = 5 # Lấy 20 trận mỗi kênh

VN_TZ = datetime.timezone(datetime.timedelta(hours=7))

GITHUB_TOKEN = os.getenv("GH_TOKEN")
REPO_NAME = os.getenv("GH_REPO", "Eternal161/dausoco")

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7",
}

LOGO_CACHE = {}

# =========================================================
# LOGO
# =========================================================

def normalize_team_name(name):
    name = re.sub(r"\bFc\b$", "FC", name)
    return name.strip()

def get_team_logo(team_name):
    if not team_name or team_name == "Unknown":
        return ""

    team_name = normalize_team_name(team_name)

    if team_name in LOGO_CACHE:
        return LOGO_CACHE[team_name]

    try:
        slug = team_name.lower().replace(" ", "-")
        url = f"https://football-logos.cc/{slug}/"
        r = requests.get(url, headers=_HEADERS, timeout=5)
        
        match = re.search(r'https://football-logos.cc/logos/[^"]+\.png', r.text)
        if match:
            logo = match.group(0)
            LOGO_CACHE[team_name] = logo
            return logo
    except:
        pass

    return f"https://ui-avatars.com/api/?name={requests.utils.quote(team_name[:2])}&size=200&background=1565C0&color=ffffff&bold=true"

# =========================================================
# PARSE MATCH TỪ URL (SIÊU CHUẨN XÁC)
# =========================================================

def parse_url_to_info(url):
    try:
        # Cắt lấy đoạn cuối cùng của URL chứa tên đội
        parts = url.rstrip('/').split('/')
        slug = ""
        for p in reversed(parts):
            if "-vs-" in p:
                slug = p.split('?')[0].split('#')[0]
                break
                
        if not slug:
            return "Unknown", "Unknown", "Chưa có lịch"

        # Loại bỏ ID trận đấu ở cuối nếu có (VD: -601445470)
        slug = re.sub(r'-\d{6,}$', '', slug)

        # Trích xuất thời gian (VD: -1545-09-05-2026)
        time_match = re.search(r"-(\d{4}-\d{2}-\d{2}-\d{4})$", slug)

        if time_match:
            t = time_match.group(1)
            thoi_gian = f"{t[0:2]}:{t[2:4]} {t[5:7]}/{t[8:10]}/{t[11:15]}"
            teams_slug = slug[:slug.rfind("-" + t)]
        else:
            thoi_gian = "Unknown"
            teams_slug = slug

        # Cắt tên 2 đội
        teams = teams_slug.split("-vs-", 1)
        doi_nha = teams[0].replace("-", " ").title().strip()
        doi_khach = teams[1].replace("-", " ").title().strip() if len(teams) > 1 else "Unknown"

        return doi_nha, doi_khach, thoi_gian
    except:
        return "Unknown", "Unknown", "Unknown"

# =========================================================
# VALIDATE M3U8
# =========================================================

def validate_m3u8(url):
    try:
        r = requests.get(url, headers=_HEADERS, timeout=8)
        return "#EXTM3U" in r.text
    except:
        return False

# =========================================================
# CAPTURE STREAM (Giữ nguyên logic cực mạnh của Dậu)
# =========================================================

def capture_stream(context, match_url):
    page = context.new_page()
    Stealth().apply_stealth_sync(page)
    streams = set()

    def handle_response(res):
        try:
            url = res.url
            ct = res.headers.get("content-type", "").lower()
            
            if ".m3u8" in url.lower() or "mpegurl" in ct:
                # Đã vá lỗi "Adelaide": Dùng "/ad/" thay vì "ads"
                if any(bad in url.lower() for bad in ["/ad/", "/ads/", "/vast/", "quangcao", "preroll", "banner"]):
                    return
                streams.add(url)
                print(f"      🎯 FOUND M3U8: {url[:60]}...")
        except:
            pass

    page.on("response", handle_response)

    try:
        # Anti bot JS
        page.add_init_script("""
        (() => {
            const origFetch = window.fetch;
            window.fetch = async (...args) => {
                if (typeof args[0] === 'string' && (args[0].includes('.m3u8') || args[0].includes('.flv'))) {
                    console.log('FETCH:', args[0]);
                }
                return origFetch(...args);
            };
            const origOpen = XMLHttpRequest.prototype.open;
            XMLHttpRequest.prototype.open = function(method, url) {
                if (url.includes('.m3u8') || url.includes('.flv')) console.log('XHR:', url);
                return origOpen.apply(this, arguments);
            };
        })();
        """)

        page.goto(match_url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(3000)

        # Xóa các lớp Overlay chống click
        try:
            page.evaluate("""
            document.querySelectorAll('*').forEach(el => {
                const s = window.getComputedStyle(el);
                if (s.position === 'fixed' && parseInt(s.zIndex) > 999) el.remove();
            });
            """)
        except: pass

        # Click Play (2 nhịp phá Pop-up)
        try:
            vp = page.viewport_size
            cx, cy = vp["width"] // 2, vp["height"] // 2
            page.mouse.click(cx, cy)
            page.wait_for_timeout(1500)
            page.mouse.click(cx, cy)
        except: pass

        # Ép Iframe Play
        for frame in page.frames:
            try:
                frame.evaluate("""
                document.querySelectorAll('video').forEach(v => {
                    v.muted = true;
                    const p = v.play();
                    if (p !== undefined) p.catch(()=>{});
                });
                """)
            except: pass

        # Đợi luồng xuất hiện
        deadline = time.time() + 15
        while time.time() < deadline:
            if streams:
                break
            time.sleep(1)

    except PWTimeout:
        print("      ⚠️ TIMEOUT")
    except Exception as e:
        print("      ❌ STREAM ERROR:", e)
    finally:
        page.close()

    # CHẤM ĐIỂM LUỒNG
    if streams:
        priority = []
        for s in streams:
            score = 0
            lower = s.lower()
            if "expire=" in lower or "sign=" in lower or "token=" in lower:
                score += 1000
            if "index.m3u8" in lower or "chunklist" in lower:
                score += 100
            priority.append((score, s))

        priority.sort(reverse=True, key=lambda x: x[0])
        best = priority[0][1]

        print(f"      ✅ FINAL STREAM: {best[:60]}...")
        if validate_m3u8(best):
            return best
        return best # Trả về luôn dù ko validate được để App tự load

    return None

# =========================================================
# JSON
# =========================================================

def create_json(all_channel_data):
    total_live = 0
    total_streams = 0
    
    for matches in all_channel_data.values():
        total_live += sum(1 for m in matches if m.get("is_live"))
        total_streams += sum(1 for m in matches if m.get("stream_url") and m["stream_url"] != WAITING_VIDEO_URL)

    data = {
        "playlist_name": "Sáng TV",
        "last_updated": datetime.datetime.now(VN_TZ).strftime("%H:%M %d/%m/%Y"),
        "total_live": total_live,
        "total_streams": total_streams,
    }
    
    # Gộp dữ liệu của Bún Chả và Hội Quán vào JSON
    data.update(all_channel_data)

    return json.dumps(data, indent=2, ensure_ascii=False)

# =========================================================
# PUSH GITHUB
# =========================================================

def push_to_github(content):
    if not GITHUB_TOKEN:
        print("⚠️ NO GH_TOKEN")
        with open(FILE_PATH, "w", encoding="utf-8") as f:
            f.write(content)
        return

    g = Github(GITHUB_TOKEN)
    repo = g.get_repo(REPO_NAME)
    msg = "⚽ Update Đa Kênh: " + datetime.datetime.now(VN_TZ).strftime("%H:%M %d/%m/%Y")

    try:
        existing = repo.get_contents(FILE_PATH)
        repo.update_file(existing.path, msg, content, existing.sha)
        print("✅ Updated GitHub")
    except:
        repo.create_file(FILE_PATH, msg, content)
        print("✅ Created GitHub file")

# =========================================================
# MAIN
# =========================================================

def scrape_and_push():
    all_channel_data = {"buncha": [], "hoiquan": []}
    
    print("=" * 70)
    print(datetime.datetime.now(VN_TZ).strftime("START %H:%M:%S %d/%m/%Y"))
    print("=" * 70)

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--autoplay-policy=no-user-gesture-required",
            ]
        )

        context = browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent=_HEADERS["User-Agent"],
            ignore_https_errors=True
        )

        # =======================================
        # BƯỚC 1 & 2: LẤY DANH SÁCH TỪNG KÊNH
        # =======================================
        for channel in CHANNELS:
            print(f"\n📺 ĐANG QUÉT KÊNH: {channel['name'].upper()}")
            page = context.new_page()
            Stealth().apply_stealth_sync(page)

            try:
                page.goto(channel["url"], wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(4000)
            except: 
                pass

            for _ in range(4):
                page.mouse.wheel(0, 3000)
                page.wait_for_timeout(1000)

            links = []
            seen = set()

            # Logic siêu chuẩn của Dậu
            for el in page.locator("a[href*='-vs-']").all():
                href = el.get_attribute("href")
                if not href or "-vs-" not in href or href in seen: continue
                
                seen.add(href)
                if not href.startswith("http"):
                    href = channel["base_url"] + href
                links.append(href)

            if LIMIT_MATCHES:
                links = links[:LIMIT_MATCHES]

            print(f"   ✅ TÌM THẤY {len(links)} TRẬN ĐẤU")

            for idx, href in enumerate(links):
                doi_nha, doi_khach, thoi_gian = parse_url_to_info(href)
                
                # Tính giờ thực tế để set is_live
                is_live, status = False, "Chưa đá ⏳"
                try:
                    match_time = datetime.datetime.strptime(thoi_gian, "%H:%M %d/%m/%Y").replace(tzinfo=VN_TZ)
                    diff_minutes = (datetime.datetime.now(VN_TZ) - match_time).total_seconds() / 60
                    
                    if -10 <= diff_minutes <= 120:
                        is_live = True
                        status = "Đang trực tiếp 🔴"
                    elif diff_minutes > 120:
                        status = "Đã kết thúc 🏁"
                except:
                    pass

                print(f"   [{idx+1}] {'🔴' if is_live else '⚪'} {doi_nha} vs {doi_khach}")

                match_info = {
                    "id": str(idx + 1),
                    "title": f"{doi_nha} vs {doi_khach}",
                    "doi_nha": doi_nha,
                    "doi_khach": doi_khach,
                    "thoi_gian": thoi_gian,
                    "trang_thai": status,
                    "is_live": is_live,
                    "logo_nha": get_team_logo(doi_nha),
                    "logo_khach": get_team_logo(doi_khach),
                    "stream_url": WAITING_VIDEO_URL,
                    "link_xem": href
                }

                all_channel_data[channel["id"]].append(match_info)

            page.close()

        # =======================================
        # BƯỚC 3: BẮT LUỒNG M3U8 (CHỈ TRẬN LIVE)
        # =======================================
        print("\n🎥 TIẾN HÀNH BẮT LUỒNG...")
        for channel in CHANNELS:
            live_matches = [m for m in all_channel_data[channel["id"]] if m["is_live"]]
            if not live_matches:
                continue
                
            print(f"\n   ► {channel['name']}: {len(live_matches)} trận Live")
            for idx, match in enumerate(live_matches):
                print(f"\n   [{idx+1}/{len(live_matches)}] Cào link: {match['title']}")
                stream = capture_stream(context, match["link_xem"])
                if stream:
                    match["stream_url"] = stream

        browser.close()

    # Đẩy lên Github
    content = create_json(all_channel_data)
    push_to_github(content)
    
    print("\n" + "=" * 70)
    print("✅ HOÀN TẤT ĐA KÊNH")
    print("=" * 70)

if __name__ == "__main__":
    scrape_and_push()
