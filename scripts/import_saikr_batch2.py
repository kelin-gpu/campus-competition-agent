#!/usr/bin/env python3
"""Import 50 new saikr competition records into event_info table."""
import os, sys, json, re, hashlib, uuid
from datetime import datetime
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
from storage.database.supabase_client import get_supabase_client

# ── 1. The 50 records: (raw_title, detail_url) ──
raw_data = [
    ("2026年全国大学生英语作文大赛", "https://www.saikr.com/vse/newccs/2026"),
    ('2026\u201c外研社\u00b7国才杯\u201d\u201c理解当代中国\u201d 外语能力公开赛-大学生竞赛-赛氪竞赛网-全国大学生比赛信息网，高含金量竞赛、权威竞赛都在赛氪', "https://www.saikr.com/vse/WYNLGKS2026"),
    ('2026年第六届《英语世界》杯全国大学生英语词汇大赛-大学生竞赛-赛氪竞赛网-全国大学生比赛信息网，高含金量竞赛、权威竞赛都在赛氪', "https://www.saikr.com/vse/vocabulary2026"),
    ('2026年第十六届APMCM亚太地区大学生数学建模竞赛（中文赛项）-大学生竞赛-赛氪竞赛网-全国大学生比赛信息网，高含金量竞赛、权威竞赛都在赛氪', "https://www.saikr.com/vse/apmcm2602"),
    ('【高校认可&加分必备！】第四届\u201cBETT杯\u201d全国大学生英语语法大赛', "https://www.saikr.com/vse/Bett-Grammar-Fourth"),
    ('【重要】本科生保送研究生（保研）定位分析（适合大一、大二、大三提前准备规划保研的同学）', "https://www.saikr.com/vse/46250"),
    ('【最后机会】第四届全国大学生汉语言文字能力大赛', "https://www.saikr.com/vse/HYY4/2026"),
    ('2026全国大学生\u201c麟创杯\u201d人工智能知识竞赛-大学生竞赛-赛氪竞赛网-全国大学生比赛信息网，高含金量竞赛、权威竞赛都在赛氪', "https://www.saikr.com/vse/LCBRGZN"),
    ('第五届全国大学生奥林匹克数学竞赛（春季赛）-大学生竞赛-赛氪竞赛网-全国大学生比赛信息网，高含金量竞赛、权威竞赛都在赛氪', "https://www.saikr.com/vse/SOIM26"),
    ('【倒计时13天丨外文局单位盖章】第五届\u201c中外传播杯\u201d大学生英语翻译大赛-大学生竞赛-赛氪竞赛网-全国大学生比赛信息网，高含金量竞赛、权威竞赛都在赛氪', "https://www.saikr.com/vse/2026NCSET"),
    ('【6月最后一场报名中】2026\u201c华青杯\u201d【AI机器人赛项】-大学生竞赛-赛氪竞赛网-全国大学生比赛信息网，高含金量竞赛、权威竞赛都在赛氪', "https://www.saikr.com/vse/DFIC2026Robot"),
    ('【创新创业类赛事】2026年第六届全国大学生技术创新创业大赛-国赛-大学生竞赛-赛氪竞赛网-全国大学生比赛信息网，高含金量竞赛、权威竞赛都在赛氪', "https://www.saikr.com/vse/JSSCSSGS266"),
    ('第二届\u201c视觉记忆\u201d全国大学生摄影大赛-大学生竞赛-赛氪竞赛网-全国大学生比赛信息网，高含金量竞赛、权威竞赛都在赛氪', "https://www.saikr.com/vse/57506"),
    ('【高含金量，强推！】第四届\u201c一带一路\u201d全国大学生英语翻译大赛', "https://www.saikr.com/vse/2026/YDYL/Translate"),
    ('【高校力荐！老师强推！】第三届全国大学生外交英语阅读大赛-大学生竞赛-赛氪竞赛网-全国大学生比赛信息网，高含金量竞赛、权威竞赛都在赛氪', "https://www.saikr.com/vse/DESC-Read-Third"),
    ('【国家一级协会主办盖章】第三届\u201c创新实践杯\u201d全国大学生英语词汇大赛-大学生竞赛-赛氪竞赛网-全国大学生比赛信息网，高含金量竞赛、权威竞赛都在赛氪', "https://www.saikr.com/vse/IPCEVC/2026"),
    ('最后10天丨多省市译协盖章\u20142026年第十二届大学生外语翻译挑战赛', "https://www.saikr.com/vse/2026/FYTZS"),
    ('2026年第七届大学生\u201c丝绸之路\u201d主题知识竞赛-大学生竞赛-赛氪竞赛网-全国大学生比赛信息网，高含金量竞赛、权威竞赛都在赛氪', "https://www.saikr.com/vse/silkroad2026"),
    ('【省赛倒计时】第八届全国大学生普通话挑战赛（NPCCS）-大学生竞赛-赛氪竞赛网-全国大学生比赛信息网，高含金量竞赛、权威竞赛都在赛氪', "https://www.saikr.com/vse/NPCCS2026"),
    ('2026年第五届全国大学生数据统计与分析竞赛-大学生竞赛-赛氪竞赛网-全国大学生比赛信息网，高含金量竞赛、权威竞赛都在赛氪', "https://www.saikr.com/vse/tjfx26"),
    ('2026年全国高校商务英语阅读大赛-大学生竞赛-赛氪竞赛网-全国大学生比赛信息网，高含金量竞赛、权威竞赛都在赛氪', "https://www.saikr.com/vse/SYR2026"),
    ('【央企盖章】2026年第五届全国青年创新翻译大赛-大学生竞赛-赛氪竞赛网-全国大学生比赛信息网，高含金量竞赛、权威竞赛都在赛氪', "https://www.saikr.com/vse/QCTranslation2026"),
    ('第二届\u201c中西挑战杯\u201d大学生医学健康知识大赛-大学生竞赛-赛氪竞赛网-全国大学生比赛信息网，高含金量竞赛、权威竞赛都在赛氪', "https://www.saikr.com/vse/YXJK2nd"),
    ('【最后19天】第三届\u201c中国故事大赛\u00b7双语中国\u201d 全国大学生外语翻译大赛', "https://www.saikr.com/vse/2026/SYFY"),
    ('【23载沉淀&老师力荐】第九届ETTBL杯全国英语阅读大赛-大学生竞赛-赛氪竞赛网-全国大学生比赛信息网，高含金量竞赛、权威竞赛都在赛氪', "https://www.saikr.com/vse/ETTBL-YD"),
    ('第十届全国大学生集成电路创新创业大赛职业技能赛项--企业命题', "https://www.saikr.com/vse/58262"),
    ('第三届全国大学生高校物理挑战赛-大学生竞赛-赛氪竞赛网-全国大学生比赛信息网，高含金量竞赛、权威竞赛都在赛氪', "https://www.saikr.com/vse/ZJWSPH2026"),
    ('【倒计时16天】第三届\u201c中国故事大赛\u00b7双语中国\u201d 全国大学生英语词汇大赛', "https://www.saikr.com/vse/2026/SYZGVC"),
    ('2026年第三届全国大学生高新技术竞赛\u2014\u2014数学竞赛-大学生竞赛-赛氪竞赛网-全国大学生比赛信息网，高含金量竞赛、权威竞赛都在赛氪', "https://www.saikr.com/vse/WLBMATH26"),
    ('全国大学生诗经吟唱大赛', "https://www.saikr.com/vse/47994"),
    ('第三届全国大学生办公软件技能大赛-大学生竞赛-赛氪竞赛网-全国大学生比赛信息网，高含金量竞赛、权威竞赛都在赛氪', "https://www.saikr.com/vse/ZJWSOS266"),
    ('2026第六届大学生算法挑战赛-大学生竞赛-赛氪竞赛网-全国大学生比赛信息网，高含金量竞赛、权威竞赛都在赛氪', "https://www.saikr.com/vse/algorithm2026"),
    ('第二届\u201c中原杯\u201d全国外语应用能力大赛-大学生竞赛-赛氪竞赛网-全国大学生比赛信息网，高含金量竞赛、权威竞赛都在赛氪', "https://www.saikr.com/vse/ZYB2026"),
    ('第四届\u201c三晋杯\u201d全国大学生英语翻译大赛-大学生竞赛-赛氪竞赛网-全国大学生比赛信息网，高含金量竞赛、权威竞赛都在赛氪', "https://www.saikr.com/vse/YSJFY"),
    ('【6月加分必备】第四届\u201c生态先锋，绿色挑战\u201d大学生环保知识大赛 \u25cf 春季赛-大学生竞赛-赛氪竞赛网-全国大学生比赛信息网，高含金量竞赛、权威竞赛都在赛氪', "https://www.saikr.com/vse/2026/AEP/Y"),
    ('2026年第二届工业互联网技术与应用挑战赛\u2014\u2014工业互联网专项赛道-大学生竞赛-赛氪竞赛网-全国大学生比赛信息网，高含金量竞赛、权威竞赛都在赛氪', "https://www.saikr.com/vse/2026Industrial"),
    ('第三届\u201c中促杯\u201d全国大学生英语翻译能力大赛-大学生竞赛-赛氪竞赛网-全国大学生比赛信息网，高含金量竞赛、权威竞赛都在赛氪', "https://www.saikr.com/vse/ZCBFY3RD"),
    ('2026\u201c国研中西杯\u201d大学生党史知识大赛-大学生竞赛-赛氪竞赛网-全国大学生比赛信息网，高含金量竞赛、权威竞赛都在赛氪', "https://www.saikr.com/vse/DSZSDS"),
    ('第四届\u201cBETT杯\u201d全国大学生英语写作大赛', "https://www.saikr.com/vse/Bett-Writing-Fourth"),
    ('第四届\u201c一带一路\u201d全国大学生英语阅读大赛', "https://www.saikr.com/vse/2026/YDYL/Read"),
    ('【额外现金奖励】第二届华夏未来数字艺术大赛-大学生竞赛-赛氪竞赛网-全国大学生比赛信息网，高含金量竞赛、权威竞赛都在赛氪', "https://www.saikr.com/vse/SZYSds"),
    ('【6月加分首推项目】（城市联赛）计算机素养赛道-大学生竞赛-赛氪竞赛网-全国大学生比赛信息网，高含金量竞赛、权威竞赛都在赛氪', "https://www.saikr.com/vse/CLComputer2026"),
    ('【端午特设赛段】第四届\u201c华文奖\u201d全国大学生文旅创作大赛', "https://www.saikr.com/vse/HWJWL1/2026"),
    ('\u201c声释中华\u201d新时代大学生外宣人才翻译大赛-大学生竞赛-赛氪竞赛网-全国大学生比赛信息网，高含金量竞赛、权威竞赛都在赛氪', "https://www.saikr.com/vse/2026WX"),
    ('【倒计时2天!】第三届\u201c讲好湾区故事\u201d 全国外语翻译大赛-大学生竞赛-赛氪竞赛网-全国大学生比赛信息网，高含金量竞赛、权威竞赛都在赛氪', "https://www.saikr.com/vse/TSGBA26V"),
    ('【最后一场】第九届\u201c远见者杯\u201d全国大学生创新促进就业大赛-职业技能素养-大学生竞赛-赛氪竞赛网-全国大学生比赛信息网，高含金量竞赛、权威竞赛都在赛氪', "https://www.saikr.com/vse/CXJYZ2026"),
    ('2026年度CCF量子计算编程挑战赛-量旋杯-大学生竞赛-赛氪竞赛网-全国大学生比赛信息网，高含金量竞赛、权威竞赛都在赛氪', "https://www.saikr.com/vse/quantum-challenge/2026"),
    ('第六届全国高校商务翻译（英语）能力挑战赛-大学生竞赛-赛氪竞赛网-全国大学生比赛信息网，高含金量竞赛、权威竞赛都在赛氪', "https://www.saikr.com/vse/BTE2026"),
    ('2026 年 \u201c高教社杯\u201d 全国大学生数学建模竞赛-大学生竞赛-赛氪竞赛网-全国大学生比赛信息网，高含金量竞赛、权威竞赛都在赛氪', "https://www.saikr.com/vse/58394"),
    ('2026年全国大学生英语作文大赛-大学生竞赛-赛氪竞赛网-全国大学生比赛信息网，高含金量竞赛、权威竞赛都在赛氪', "https://m.saikr.com/vse/newccs/2026"),
]


def clean_title(raw):
    """Remove saikr suffix and marketing prefixes."""
    t = raw.strip()
    # Remove saikr suffix patterns
    t = re.sub(r'-大学生竞赛-赛氪竞赛网.*$', '', t)
    t = re.sub(r'-国赛-大学生竞赛-赛氪竞赛网.*$', '', t)
    t = re.sub(r'-职业技能素养-大学生竞赛-赛氪竞赛网.*$', '', t)
    # Remove marketing prefixes like 【xxx】
    t = re.sub(r'^【[^】]*】\s*', '', t)
    # Remove leading "最后10天丨" style prefixes
    t = re.sub(r'^最后\d+天丨\s*', '', t)
    # Remove em-dash prefix like "—2026年"
    t = re.sub(r'^[\u2014\u2015\u2010-]+\s*', '', t)
    # Normalize whitespace
    t = re.sub(r'\s+', ' ', t).strip()
    return t


def normalize(s):
    """For fuzzy comparison - strip all non-essential chars."""
    s = re.sub(r'[\s\u3000\u00b7\u30fb\-\u2014\u2015\u2010\u00b7]+', '', s)
    s = re.sub(r'[（）\(\)【】\[\]《》<>""\"\'\'\u201c\u201d\u2018\u2019]', '', s)
    return s.lower()


def is_similar(a, b, threshold=0.7):
    """Simple similarity based on common characters."""
    na, nb = normalize(a), normalize(b)
    if na == nb:
        return True
    if na in nb or nb in na:
        return True
    sa, sb = set(na), set(nb)
    if not sa or not sb:
        return False
    overlap = len(sa & sb) / max(len(sa), len(sb))
    return overlap > threshold


def gen_event_id():
    return 'SAI2-' + hashlib.md5(uuid.uuid4().bytes).hexdigest()[:8].upper()


def infer_category(title):
    """Infer category from title keywords."""
    t = title.lower()
    if any(k in t for k in ['翻译', '英语', '外语', '阅读', '写作', '语法', '词汇', '普通话', '商务英语', '外宣']):
        return '英语/语言类'
    if any(k in t for k in ['数学建模', '数学', '奥林匹克数学']):
        return '数学类'
    if any(k in t for k in ['人工智能', 'ai', '机器人', '算法', '编程', '量子计算', '计算机', '软件', '集成电路', '工业互联网', '数据', '办公']):
        return '信息技术/AI类'
    if any(k in t for k in ['创新创业', '创业', '就业']):
        return '创新创业类'
    if any(k in t for k in ['摄影', '数字艺术', '文旅', '创作', '诗经', '吟唱']):
        return '文化艺术类'
    if any(k in t for k in ['环保', '生态', '绿色']):
        return '环保类'
    if any(k in t for k in ['医学', '健康']):
        return '医药健康类'
    if any(k in t for k in ['物理']):
        return '理工类'
    if any(k in t for k in ['保研']):
        return '职业发展类'
    if any(k in t for k in ['党史', '知识', '丝绸之路']):
        return '知识竞赛类'
    if any(k in t for k in ['汉语言', '文字']):
        return '语言文学类'
    return '综合类'


def main():
    # Step 1: Clean titles and deduplicate within the 50
    cleaned = []
    seen_urls = set()
    for raw_title, url in raw_data:
        ct = clean_title(raw_title)
        # Deduplicate: same URL base (normalize m. -> www.)
        url_base = url.replace('m.saikr.com', 'www.saikr.com')
        if url_base in seen_urls:
            print(f"  SKIP internal dup: {ct[:40]}")
            continue
        seen_urls.add(url_base)
        cleaned.append((ct, url))

    print(f"Raw: {len(raw_data)} -> Cleaned: {len(cleaned)} (after internal dedup)")

    # Step 2: Load existing DB records
    client = get_supabase_client()
    resp = client.table('event_info').select('event_id,title,source_name,source_url').execute()
    db_records = resp.data
    print(f"DB total records: {len(db_records)}")

    # Step 3: Match against existing
    matched_existing = []
    new_records = []

    for ct, url in cleaned:
        found = False
        for rec in db_records:
            if is_similar(ct, rec['title']):
                matched_existing.append((ct, url, rec['event_id'], rec['title'], rec['source_url']))
                found = True
                break
        if not found:
            new_records.append((ct, url))

    print(f"Matched existing: {len(matched_existing)}")
    print(f"New to insert: {len(new_records)}")

    # Step 4: Update source_url for matched records
    updated_count = 0
    for new_title, new_url, existing_id, existing_title, old_url in matched_existing:
        if old_url != new_url:
            client.table('event_info').update({'source_url': new_url}).eq('event_id', existing_id).execute()
            updated_count += 1
            print(f"  URL-UPDATE: {existing_title[:40]}")
            print(f"    OLD: {old_url}")
            print(f"    NEW: {new_url}")

    print(f"\nUpdated source_url: {updated_count}")

    # Step 5: Insert new records
    inserted_count = 0
    for title, url in new_records:
        eid = gen_event_id()
        cat = infer_category(title)
        record = {
            'event_id': eid,
            'title': title,
            'scope_type': '校外竞赛',
            'category': cat,
            'summary': None,
            'signup_deadline': None,
            'event_time': None,
            'target_major': '所有专业',
            'target_grade': '大一,大二,大三,大四',
            'contest_level': '其他',
            'tags': json.dumps(['赛氪热门'], ensure_ascii=False),
            'policy_tags': None,
            'source_name': '赛氪',
            'source_url': url,
            'authority_level': '中',
            'status': '报名中',
            'organizer': None,
            'update_time': datetime.now().isoformat(),
            'original_text': None,
            'is_ministry_approved': False,
        }
        client.table('event_info').insert(record).execute()
        inserted_count += 1
        print(f"  INSERT: [{cat}] {title[:50]}")

    print(f"\nInserted: {inserted_count}")

    # Step 6: Update saikr_processed.json
    json_path = os.path.join(os.path.dirname(__file__), '..', 'assets', 'data', 'saikr_processed.json')
    with open(json_path, 'r', encoding='utf-8') as f:
        existing_json = json.load(f)

    existing_urls = set(r.get('detail_url', '') for r in existing_json)
    added_json = 0
    for title, url in new_records:
        if url not in existing_urls:
            existing_json.append({
                'title': title,
                'detail_url': url,
                'source_url': 'https://www.saikr.com/index/hot/contest',
                'source': 'saikr'
            })
            existing_urls.add(url)
            added_json += 1

    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(existing_json, f, ensure_ascii=False, indent=2)

    print(f"\nsaikr_processed.json: +{added_json}, total={len(existing_json)}")

    # Step 7: Final stats
    skipped = len(cleaned) - len(matched_existing) - len(new_records)
    print("\n" + "=" * 60)
    print(f"IMPORT SUMMARY:")
    print(f"  Raw input:           {len(raw_data)}")
    print(f"  After internal dedup:{len(cleaned)}")
    print(f"  Matched existing:    {len(matched_existing)}")
    print(f"    - URL updated:     {updated_count}")
    print(f"    - URL unchanged:   {len(matched_existing) - updated_count}")
    print(f"  New inserted:        {inserted_count}")
    print(f"  Skipped (full dup):  {skipped}")
    print("=" * 60)


if __name__ == '__main__':
    main()
