#!/usr/bin/env python3
"""
Step 1: Revert all bad URL fixes from previous run.
Step 2: Re-fix with precise matching.
"""
import os, sys, json, re, time, subprocess
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
from storage.database.supabase_client import get_supabase_client
from urllib.parse import quote

# Known correct URLs from second batch (SAI2-*) and first batch that were originally working
# These should NOT be changed
VERIFIED_WORKING = {
    # Second batch - all 43 verified 200
    # First batch originally working
    "https://www.saikr.com/vse/lanqiao/2026",
    "https://www.saikr.com/vse/neccs/2026",
    "https://www.saikr.com/vse/smartcar/2026",
}

# URLs that the fix script wrongly assigned (all redirect to homepage)
BAD_URLS = {
    "https://www.saikr.com/vse/59441",
    "https://www.saikr.com/vse/apmcm2601",
    "https://www.saikr.com/vse/2026BMKIC",
    "https://www.saikr.com/vse/Mental202602",
}


def search_saikr_precise(keyword: str) -> list:
    """Search saikr.com, return list of (url, title) with dedup."""
    url = f"https://www.saikr.com/search/0?keyword={quote(keyword)}"
    try:
        result = subprocess.run(
            ['curl', '-s', '-L', '--max-time', '10', url],
            capture_output=True, text=True, timeout=15
        )
        html = result.stdout
        # Extract from <a> tags: href="https://www.saikr.com/vse/xxx" ... title="YYY"
        matches = re.findall(
            r'href="(https://www\.saikr\.com/vse/[^"]+)"[^>]*title="([^"]+)"',
            html
        )
        seen = set()
        results = []
        for full_url, title in matches:
            title = title.strip()
            if title and full_url not in seen:
                seen.add(full_url)
                results.append((full_url, title))
        return results
    except Exception as e:
        return []


def extract_core_name(title: str) -> str:
    """Extract the core competition name, stripping year/edition/decorations."""
    t = title
    # Remove marketing prefixes
    t = re.sub(r'^【[^】]*】\s*', '', t)
    t = re.sub(r'^最后\d+天[丨|]\s*', '', t)
    # Remove year
    t = re.sub(r'2026年?\s*', '', t)
    t = re.sub(r'2025年?\s*', '', t)
    # Remove edition
    t = re.sub(r'第[一二三四五六七八九十百\d]+届', '', t)
    # Remove common suffixes from saikr
    t = re.sub(r'-大学生竞赛-赛氪竞赛网.*$', '', t)
    t = re.sub(r'全国大学生比赛信息网.*$', '', t)
    # Remove parenthetical
    t = re.sub(r'[（(][^）)]*[）)]', '', t)
    # Normalize
    t = re.sub(r'\s+', '', t)
    t = re.sub(r'[""\'\'""·\-\—\·]', '', t)
    return t


def precise_match(db_title: str, search_results: list) -> str:
    """
    Only accept a match if the search result title contains the core 
    competition name from the DB title, or vice versa.
    """
    if not search_results:
        return ''
    
    core_db = extract_core_name(db_title)
    if len(core_db) < 3:
        return ''
    
    for url, res_title in search_results:
        core_res = extract_core_name(res_title)
        
        # Core name must be substantially contained in the other
        if len(core_db) >= 4 and len(core_res) >= 4:
            if core_db in core_res or core_res in core_db:
                return url
            # Check if at least 70% of core_db chars are in core_res in order
            # Simple subsequence check
            idx = 0
            matched = 0
            for c in core_db:
                pos = core_res.find(c, idx)
                if pos >= 0:
                    matched += 1
                    idx = pos + 1
            if matched / len(core_db) >= 0.7:
                return url
    
    return ''


def verify_url(url: str) -> bool:
    """Verify URL returns 200 and is not an error page."""
    try:
        result = subprocess.run(
            ['curl', '-s', '-L', '--max-time', '8', url],
            capture_output=True, text=True, timeout=12
        )
        if '学校或者竞赛不存在' in result.stdout:
            return False
        # Check it doesn't redirect to homepage
        if '<title>赛氪 - 全国大学生竞赛活动平台' in result.stdout:
            return False
        # Check HTTP code
        result2 = subprocess.run(
            ['curl', '-s', '-o', '/dev/null', '-w', '%{http_code}', '-L', '--max-time', '8', url],
            capture_output=True, text=True, timeout=12
        )
        return result2.stdout.strip() == '200'
    except:
        return False


def main():
    client = get_supabase_client()
    
    # ── STEP 1: Revert bad URLs ──
    print("=" * 60)
    print("STEP 1: Reverting bad URL assignments...")
    print("=" * 60)
    
    resp = client.table('event_info').select('event_id,title,source_name,source_url').execute()
    all_records = resp.data
    
    reverted = 0
    for r in all_records:
        url = r['source_url'] or ''
        if url in BAD_URLS:
            # Revert to original (we'll set to empty for now, will re-fix)
            client.table('event_info').update({'source_url': ''}).eq('event_id', r['event_id']).execute()
            reverted += 1
            print(f"  REVERT: {r['title'][:40]} (was {url})")
    
    print(f"\nReverted: {reverted}")
    
    # ── STEP 2: Identify all records still needing fix ──
    print("\n" + "=" * 60)
    print("STEP 2: Identifying records needing fix...")
    print("=" * 60)
    
    # Re-query after reverts
    resp = client.table('event_info').select('event_id,title,source_name,source_url').execute()
    all_records = resp.data
    
    need_fix = []
    for r in all_records:
        src = r['source_name'] or ''
        url = r['source_url'] or ''
        
        if src in ('赛氪', '赛氪网', '赛客'):
            if not url or url in BAD_URLS:
                need_fix.append(r)
            elif '/comp/' in url:
                need_fix.append(r)
            else:
                # Test if URL is valid
                try:
                    result = subprocess.run(
                        ['curl', '-s', '-L', '--max-time', '5', url],
                        capture_output=True, text=True, timeout=8
                    )
                    if '学校或者竞赛不存在' in result.stdout or '<title>赛氪 - 全国大学生竞赛活动平台' in result.stdout:
                        need_fix.append(r)
                except:
                    need_fix.append(r)
    
    print(f"Records needing fix: {len(need_fix)}")
    for r in need_fix:
        print(f"  {r['event_id']} | {r['title'][:45]} | {r['source_url'] or 'EMPTY'}")
    
    # ── STEP 3: Search and fix each ──
    print("\n" + "=" * 60)
    print("STEP 3: Searching saikr.com for real URLs...")
    print("=" * 60)
    
    fixed = 0
    not_found = []
    
    for i, r in enumerate(need_fix):
        title = r['title']
        
        # Build search keyword - use core competition name
        keyword = extract_core_name(title)
        # If too short, use more of the original title
        if len(keyword) < 6:
            keyword = re.sub(r'^【[^】]*】', '', title)
            keyword = re.sub(r'-大学生竞赛.*$', '', keyword)
            keyword = keyword[:25]
        
        print(f"\n[{i+1}/{len(need_fix)}] {title[:50]}")
        print(f"  Searching: {keyword}")
        
        results = search_saikr_precise(keyword)
        print(f"  Found {len(results)} results")
        
        best_url = precise_match(title, results)
        
        # If no match, try alternative keywords
        if not best_url:
            # Try with just the unique part of the name
            alt_keyword = re.sub(r'全国|大学生|2026|年', '', title)
            alt_keyword = re.sub(r'第[一二三四五六七八九十\d]+届', '', alt_keyword)
            alt_keyword = alt_keyword[:20].strip()
            if alt_keyword and alt_keyword != keyword:
                results2 = search_saikr_precise(alt_keyword)
                if results2:
                    best_url = precise_match(title, results2)
                    if best_url:
                        print(f"  Matched with alt keyword: {alt_keyword}")
        
        if best_url and verify_url(best_url):
            client.table('event_info').update({
                'source_url': best_url
            }).eq('event_id', r['event_id']).execute()
            fixed += 1
            print(f"  ✅ FIXED: {best_url}")
        else:
            not_found.append(r)
            print(f"  ❌ NOT FOUND")
            if results:
                for u, t in results[:3]:
                    print(f"     Candidate: {t[:40]}")
        
        time.sleep(0.5)
    
    # ── Summary ──
    print("\n" + "=" * 60)
    print(f"FINAL SUMMARY:")
    print(f"  Reverted bad URLs: {reverted}")
    print(f"  Fixed:             {fixed}")
    print(f"  Not found:         {len(not_found)}")
    print("=" * 60)
    
    if not_found:
        print(f"\nNot found ({len(not_found)}):")
        for r in not_found:
            print(f"  {r['event_id']} | {r['title'][:50]}")


if __name__ == '__main__':
    main()
