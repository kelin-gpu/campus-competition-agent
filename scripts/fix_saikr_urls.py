#!/usr/bin/env python3
"""
Fix invalid saikr source_urls by searching saikr.com for each competition title.
"""
import os, sys, json, re, time, subprocess
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
from storage.database.supabase_client import get_supabase_client
from urllib.parse import quote


def search_saikr(keyword: str) -> list:
    """Search saikr.com and return list of (url, title) tuples."""
    url = f"https://www.saikr.com/search/0?keyword={quote(keyword)}"
    try:
        result = subprocess.run(
            ['curl', '-s', '-L', '--max-time', '10', url],
            capture_output=True, text=True, timeout=15
        )
        html = result.stdout
        # Extract title + URL from <a> tags with title attribute
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
        print(f"    Search error for '{keyword}': {e}")
        return []


def normalize(s):
    """Normalize string for comparison."""
    s = re.sub(r'[\s\u3000\u00b7\u30fb\-\u2014\u2015\u2010\u00b7\u25cf]+', '', s)
    s = re.sub(r'[（）\(\)【】\[\]《》<>""\"\'\'\u201c\u201d\u2018\u2019\u00b7]', '', s)
    return s.lower()


def extract_keyword(title: str) -> str:
    """Extract a good search keyword from competition title."""
    t = title.strip()
    # Remove marketing prefixes
    t = re.sub(r'^【[^】]*】\s*', '', t)
    t = re.sub(r'^最后\d+天丨\s*', '', t)
    # Remove year prefix for shorter keyword
    t = re.sub(r'^2026年?\s*', '', t)
    # Remove common suffixes
    t = re.sub(r'-大学生竞赛-赛氪竞赛网.*$', '', t)
    t = re.sub(r'全国大学生比赛信息网.*$', '', t)
    # Remove edition numbers like 第X届
    t_short = re.sub(r'第[一二三四五六七八九十\d]+届', '', t)
    # Use the shorter version if it's still meaningful
    if len(t_short) > 5:
        t = t_short
    # Truncate to reasonable length
    if len(t) > 30:
        t = t[:30]
    return t.strip()


def match_result(db_title: str, search_results: list) -> str:
    """Find the best matching URL from search results for a given DB title."""
    if not search_results:
        return ''
    
    norm_db = normalize(db_title)
    
    # Score each result
    best_url = ''
    best_score = 0
    
    for url, res_title in search_results:
        norm_res = normalize(res_title)
        
        # Exact match
        if norm_db == norm_res:
            return url
        
        # Check containment
        score = 0
        if norm_db in norm_res or norm_res in norm_db:
            score = 0.8
        else:
            # Character overlap
            s1, s2 = set(norm_db), set(norm_res)
            if s1 and s2:
                overlap = len(s1 & s2) / max(len(s1), len(s2))
                score = overlap
        
        # Boost if key identifiers match
        # Extract core name (remove year, edition number)
        core_db = re.sub(r'2026|2025|第[一二三四五六七八九十\d]+届|全国|大学生', '', norm_db)
        core_res = re.sub(r'2026|2025|第[一二三四五六七八九十\d]+届|全国|大学生', '', norm_res)
        if core_db and core_res and (core_db in core_res or core_res in core_db):
            score += 0.3
        
        if score > best_score:
            best_score = score
            best_url = url
    
    # Only accept if score is high enough
    if best_score >= 0.5:
        return best_url
    return ''


def verify_url(url: str) -> bool:
    """Verify URL returns 200."""
    try:
        result = subprocess.run(
            ['curl', '-s', '-o', '/dev/null', '-w', '%{http_code}', '-L', '--max-time', '8', url],
            capture_output=True, text=True, timeout=12
        )
        code = result.stdout.strip()
        if code == '200':
            # Also check it's not an error page
            result2 = subprocess.run(
                ['curl', '-s', '-L', '--max-time', '8', url],
                capture_output=True, text=True, timeout=12
            )
            if '学校或者竞赛不存在' in result2.stdout:
                return False
            return True
        return False
    except:
        return False


def main():
    client = get_supabase_client()
    
    # Get all saikr records that need fixing
    # 1. First batch: source_name='赛氪' with /vse/ URLs (need to test which are broken)
    # 2. source_name='赛氪网' or '赛客' with /comp/ URLs
    
    resp = client.table('event_info').select('event_id,title,source_name,source_url').execute()
    all_records = resp.data
    
    # Identify records to fix
    to_fix = []
    
    for r in all_records:
        src = r['source_name'] or ''
        url = r['source_url'] or ''
        
        if src in ('赛氪网', '赛客') and '/comp/' in url:
            to_fix.append(r)
        elif src == '赛氪' and '/vse/' in url:
            # Will test below
            to_fix.append(r)
    
    print(f"Total saikr records to check: {len(to_fix)}")
    
    # First, test which /vse/ URLs are actually broken
    need_fix = []
    already_ok = []
    
    for r in to_fix:
        url = r['source_url']
        if '/comp/' in url:
            need_fix.append(r)
            continue
        
        # Test the URL
        try:
            result = subprocess.run(
                ['curl', '-s', '-L', '--max-time', '6', url],
                capture_output=True, text=True, timeout=10
            )
            if '学校或者竞赛不存在' in result.stdout:
                need_fix.append(r)
            else:
                already_ok.append(r)
        except:
            need_fix.append(r)
    
    print(f"Already OK: {len(already_ok)}")
    print(f"Need fix: {len(need_fix)}")
    print()
    
    # Fix each record
    fixed = 0
    not_found = []
    
    for i, r in enumerate(need_fix):
        title = r['title']
        old_url = r['source_url']
        keyword = extract_keyword(title)
        
        print(f"[{i+1}/{len(need_fix)}] {title[:45]}")
        print(f"  Keyword: {keyword}")
        
        # Search saikr
        results = search_saikr(keyword)
        print(f"  Search results: {len(results)}")
        
        if results:
            # Try to match
            best_url = match_result(title, results)
            
            if not best_url and results:
                # If no good match, try with different keyword
                keyword2 = re.sub(r'第[一二三四五六七八九十\d]+届', '', title)
                keyword2 = keyword2[:25]
                results2 = search_saikr(keyword2)
                if results2:
                    best_url = match_result(title, results2)
                    if results2 and not best_url:
                        # Show what we found for debugging
                        for u, t in results2[:3]:
                            print(f"    Candidate: {t[:40]} -> {u}")
            
            if best_url:
                # Verify
                if verify_url(best_url):
                    # Update DB
                    client.table('event_info').update({
                        'source_url': best_url
                    }).eq('event_id', r['event_id']).execute()
                    fixed += 1
                    print(f"  FIXED: {best_url}")
                else:
                    print(f"  URL found but invalid: {best_url}")
                    not_found.append(r)
            else:
                print(f"  NO MATCH")
                not_found.append(r)
        else:
            print(f"  NO SEARCH RESULTS")
            not_found.append(r)
        
        # Rate limiting
        time.sleep(0.5)
    
    # Summary
    print("\n" + "=" * 60)
    print(f"FIX SUMMARY:")
    print(f"  Total checked:  {len(to_fix)}")
    print(f"  Already OK:     {len(already_ok)}")
    print(f"  Fixed:          {fixed}")
    print(f"  Not found:      {len(not_found)}")
    print("=" * 60)
    
    if not_found:
        print(f"\nNot found records:")
        for r in not_found:
            print(f"  {r['event_id']} | {r['title'][:50]} | {r['source_url']}")
    
    # Update saikr_processed.json
    json_path = os.path.join(os.path.dirname(__file__), '..', 'assets', 'data', 'saikr_processed.json')
    with open(json_path, 'r', encoding='utf-8') as f:
        kb_data = json.load(f)
    
    # Build event_id -> new_url mapping for fixed records
    # Re-query to get updated URLs
    resp2 = client.table('event_info').select('event_id,title,source_url').execute()
    url_map = {r['event_id']: r['source_url'] for r in resp2.data}
    
    updated_json = 0
    for item in kb_data:
        # Try to match by title
        for r in need_fix:
            if r['title'] in item.get('title', '') or item.get('title', '') in r['title']:
                new_url = url_map.get(r['event_id'], '')
                if new_url and new_url != item.get('detail_url', ''):
                    item['detail_url'] = new_url
                    updated_json += 1
    
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(kb_data, f, ensure_ascii=False, indent=2)
    
    print(f"\nsaikr_processed.json updated: {updated_json} records")


if __name__ == '__main__':
    main()
