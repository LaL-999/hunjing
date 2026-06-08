"""
extract_and_clean.py — 从《红楼梦脂评汇校本.epub》提取并清洗 OCR 文本

输入:
    huimeng/红楼梦脂评汇校本.epub

输出:
    huimeng/data/extracted/红楼梦_full.txt   清洗后全文
    huimeng/data/chapters/ch01.txt ~ ch80.txt 按回切分
    huimeng/data/chapters/_meta.json          各回元数据

清洗规则:
    1. 解压 epub → 临时目录
    2. 按 spine 顺序读取每个 page_N.html
    3. 用 HTMLParser 抽取纯文本,移除 "Page N" 标记 与 OCR 准确度声明
    4. 移除每页 "抚琴居 红楼梦脂评汇校本" 页眉(各种空格变体)
    5. 合并 CJK 字符之间的 OCR 空格
    6. 修正高频 OCR 错字
    7. 用"页起始命中第X回头"识别真章节(过滤目录与脂批交叉引用)

设计选择:
    - 不剥离脂批:demo 阶段保留,作为 agent 的额外人物分析素材
    - OCR 错字修正只改"我能确认且不会误伤合法用例"的字
    - 元数据落盘,后续 GraphRAG 阶段可按章节定位原文
"""

import os
import re
import json
import shutil
import zipfile
from html.parser import HTMLParser

# ---------- 路径配置 ----------

PROJECT_ROOT = r"C:\Users\Administrator\Desktop\huimeng"
EPUB_FILE = os.path.join(PROJECT_ROOT, "红楼梦脂评汇校本.epub")
TEMP_DIR = os.path.join(PROJECT_ROOT, "_epub_temp")
EXTRACTED_DIR = os.path.join(PROJECT_ROOT, "data", "extracted")
CHAPTERS_DIR = os.path.join(PROJECT_ROOT, "data", "chapters")

# ---------- OCR 错字修正表 ----------
# 只放"100% 确认且不会误伤"的修正
OCR_FIXES = [
    ("睛雯", "晴雯"),         # 睛(目部) vs 晴(日部)
    ("贾旭", "贾琏"),
    ("页旭", "贾琏"),
    ("苞砚", "宝玉"),         # 抚琴居 OCR 把 "宝玉" 偶尔识别为 "苞砚"
    ("硕芍", "宝钗"),         # 同上,"宝钗" → "硕芍"
    # "风姐" → "凤姐" 不做自动修正,因 "风" 字在文中合法出现频繁
]

# ---------- HTML 文本抽取 ----------

class _TextExtractor(HTMLParser):
    """轻量 HTML 文本抽取器,跳过 head/script/style,在块标签处插换行."""

    def __init__(self):
        super().__init__()
        self._buf = []
        self._skip = False

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "head"):
            self._skip = True
        elif tag in ("p", "br", "div"):
            self._buf.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "head"):
            self._skip = False
        elif tag == "p":
            self._buf.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self._buf.append(data)

    def get_text(self):
        return "".join(self._buf)


def extract_page_text(html_path):
    """提取单页 HTML 的纯文本,移除 'Page N' 与 OCR 准确度声明."""
    with open(html_path, "r", encoding="utf-8") as f:
        content = f.read()
    ex = _TextExtractor()
    ex.feed(content)
    text = ex.get_text()
    text = re.sub(r"^\s*Page\s*\d+", "", text, count=1)
    text = re.sub(r"The text on this page.*?accurate", "", text, flags=re.DOTALL)
    return text.strip()


# ---------- 文本清洗 ----------

# CJK 字符 + 中文标点 + 全角符号
CJK_RANGE = r"[一-鿿　-〿＀-￯]"

# 页眉模式:抚琴居 红楼梦 脂评 汇校 本(各种空格组合)
HEADER_PATTERN = re.compile(
    r"抚\s*琴\s*居\s*红\s*楼\s*梦\s*脂\s*评\s*汇\s*校\s*本"
)

# OCR 字间空格(CJK + 标点 之间的单个空格)
INTERCHAR_SPACE = re.compile(rf"(?<={CJK_RANGE})[ \t]+(?={CJK_RANGE})")


def cleanup(text):
    """对单页或全文应用清洗规则."""
    text = HEADER_PATTERN.sub("", text)
    text = INTERCHAR_SPACE.sub("", text)
    for wrong, right in OCR_FIXES:
        text = text.replace(wrong, right)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# ---------- 章节识别 ----------

CN_DIGIT = {
    "零": 0, "一": 1, "二": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
}

# OCR 把"八"识别为"入"是高频错误(18/28/38/58/68/78 全栽在这上面),
# 把这两个字符在数字位上视为同义。
DIGIT_OCR_ALIASES = str.maketrans({"入": "八"})

# OCR 把"回"识别为下列字符也常见(酉/同/司/囘/曰),在章节头位置上等价处理。
HUI_VARIANTS = "回司同囘酉曰"


def cn_to_num(cn):
    """中文数字 → 阿拉伯数字。支持 1-99,并修正 OCR 高频别字。"""
    cn = cn.replace(" ", "").translate(DIGIT_OCR_ALIASES)
    if cn == "十":
        return 10
    if cn.startswith("十"):
        return 10 + CN_DIGIT.get(cn[1:], 0)
    if "十" in cn:
        a, _, b = cn.partition("十")
        return CN_DIGIT.get(a, 0) * 10 + (CN_DIGIT.get(b, 0) if b else 0)
    return CN_DIGIT.get(cn, -1)


# 页首章节标记:清洗后的文本以"第X回"打头
# digit 类含 OCR 别字"入";"回" 用 HUI_VARIANTS 集合
CHAPTER_HEAD_RE = re.compile(rf"^第([一二三四五六七八九十零入]{{1,4}})[{HUI_VARIANTS}]")

# 页内任意位置的章节头(用于 TOC 检测)
ANY_CHAPTER_REF_RE = re.compile(rf"第[一二三四五六七八九十零入]{{1,4}}[{HUI_VARIANTS}]")

# 同一页出现 ≥ TOC_REF_THRESHOLD 个章节头视为目录页或脂批密集页,跳过
TOC_REF_THRESHOLD = 2


def detect_chapter_starts(pages_text):
    """
    识别真章节起始页。难点是同一 epub 中 TOC 页与正文页交错(TOC 在 pages_text[9-11],
    正文章一在 pages_text[14],无法用"跳过前 N 页"简单切分)。

    策略:对每页统计章节头出现次数。
        - 若一页出现 ≥ TOC_REF_THRESHOLD 个章节头 → 认作 TOC/脂批密集页,整页跳过
        - 否则若该页以章节头打头 → 认作章节起始

    设计:
    - "回" 用变体集合(回司同囘酉曰),"八" 容忍别字"入"
    - 每个章号取首次命中
    - 检测后做单调性校验

    Returns:
        dict {chapter_num: page_index}
    """
    chapter_to_page = {}
    for i, text in enumerate(pages_text):
        if len(text) < 50:
            continue
        # 跳过目录页/脂批密集页
        if len(ANY_CHAPTER_REF_RE.findall(text)) >= TOC_REF_THRESHOLD:
            continue
        head = text.lstrip()[:80]
        m = CHAPTER_HEAD_RE.match(head)
        if not m:
            continue
        num = cn_to_num(m.group(1))
        if 1 <= num <= 80 and num not in chapter_to_page:
            chapter_to_page[num] = i

    # 单调性校验
    sorted_pairs = sorted(chapter_to_page.items())
    for j in range(1, len(sorted_pairs)):
        prev_num, prev_idx = sorted_pairs[j - 1]
        cur_num, cur_idx = sorted_pairs[j]
        if cur_idx <= prev_idx:
            print(
                f"      ⚠️  单调性破坏:第 {prev_num} 回 @ {prev_idx} → "
                f"第 {cur_num} 回 @ {cur_idx}(后者应大于前者)"
            )
    return chapter_to_page


# ---------- epub 解压 ----------

def unzip_epub():
    """解压 epub 到 _epub_temp/。每次运行都重新解压,确保干净。"""
    if os.path.isdir(TEMP_DIR):
        shutil.rmtree(TEMP_DIR)
    os.makedirs(TEMP_DIR, exist_ok=True)
    with zipfile.ZipFile(EPUB_FILE, "r") as zf:
        zf.extractall(TEMP_DIR)


def cleanup_temp():
    """删除临时解压目录。"""
    if os.path.isdir(TEMP_DIR):
        shutil.rmtree(TEMP_DIR)


# ---------- 主流程 ----------

def main():
    os.makedirs(EXTRACTED_DIR, exist_ok=True)
    os.makedirs(CHAPTERS_DIR, exist_ok=True)

    print(f"[0/5] 解压 epub → {TEMP_DIR}")
    unzip_epub()
    epub_root = os.path.join(TEMP_DIR, "EPUB")
    if not os.path.isdir(epub_root):
        # 部分 epub 包结构不同,尝试根目录
        epub_root = TEMP_DIR

    # 1. 读取 OPF 拿 spine 顺序
    print("[1/5] 解析 OPF spine 顺序")
    opf_path = os.path.join(epub_root, "content.opf")
    with open(opf_path, "r", encoding="utf-8") as f:
        opf = f.read()
    spine_ids = re.findall(r'<itemref[^>]+idref="([^"]+)"', opf)
    # OPF 中 <item> 的属性顺序不固定(href 与 id 谁先谁后都可能),分别抓后再合并
    item_blocks = re.findall(r"<item\s[^>]+/>", opf)
    items = {}
    for block in item_blocks:
        id_match = re.search(r'\bid="([^"]+)"', block)
        href_match = re.search(r'\bhref="([^"]+)"', block)
        if id_match and href_match:
            items[id_match.group(1)] = href_match.group(1)
    print(f"      spine 共 {len(spine_ids)} 项,manifest 共 {len(items)} 项")

    # 2. 提取并清洗每页文本(逐页处理,便于章节定位用页索引)
    print("[2/5] 抽取并清洗每页文本")
    pages_text = []
    skipped = 0
    for sid in spine_ids:
        href = items.get(sid)
        if not href:
            pages_text.append("")
            skipped += 1
            continue
        path = os.path.join(epub_root, href.replace("/", os.sep))
        if not os.path.exists(path):
            pages_text.append("")
            skipped += 1
            continue
        raw = extract_page_text(path)
        cleaned = cleanup(raw)
        pages_text.append(cleaned)
    print(f"      已处理 {len(pages_text)} 页(跳过 {skipped} 页非内容项)")

    # 3. 拼接全文 + 写盘
    print("[3/5] 写入全文")
    full_text = "\n\n".join(p for p in pages_text if p)
    full_path = os.path.join(EXTRACTED_DIR, "红楼梦_full.txt")
    with open(full_path, "w", encoding="utf-8") as f:
        f.write(full_text)
    print(f"      → {full_path} ({len(full_text):,} 字)")

    # 4. 切章
    print("[4/5] 识别并切分章节")
    chapter_to_page = detect_chapter_starts(pages_text)
    print(f"      识别到 {len(chapter_to_page)} 个章节起始页")

    if 74 not in chapter_to_page:
        print("      ⚠️  第 74 回未识别到!请人工核查 page 897 附近")
    else:
        print(f"      第 74 回起始页 → page_{chapter_to_page[74]}")

    sorted_chapters = sorted(chapter_to_page.items())
    meta = []
    for i, (num, start_page) in enumerate(sorted_chapters):
        end_page = (
            sorted_chapters[i + 1][1]
            if i + 1 < len(sorted_chapters)
            else len(pages_text)
        )
        chapter_text = "\n\n".join(p for p in pages_text[start_page:end_page] if p)
        fname = f"ch{num:02d}.txt"
        out_path = os.path.join(CHAPTERS_DIR, fname)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(chapter_text)
        meta.append({
            "chapter": num,
            "file": fname,
            "page_range": [start_page, end_page - 1],
            "char_count": len(chapter_text),
        })

    # 5. 元数据
    print("[5/5] 写入元数据")
    meta_path = os.path.join(CHAPTERS_DIR, "_meta.json")
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "source": "红楼梦脂评汇校本.epub (抚琴居版)",
                "total_chapters": len(meta),
                "chapters": meta,
            },
            f,
            ensure_ascii=False,
            indent=2,
        )
    print(f"      → {len(meta)} 个章节文件 + 元数据")

    # 清理临时目录
    cleanup_temp()
    print(f"      已清理 {TEMP_DIR}")

    # 摘要
    print("\n=== 处理完成 ===")
    print(f"全文      :{len(full_text):,} 字")
    print(f"章节      :{len(meta)} 回")
    missing = [n for n in range(1, 81) if n not in chapter_to_page]
    if missing:
        print(f"未识别章节:{missing}")
    ch74 = next((m for m in meta if m["chapter"] == 74), None)
    if ch74:
        print(f"第 74 回   :{ch74['char_count']:,} 字 "
              f"(page {ch74['page_range'][0]}-{ch74['page_range'][1]})")


if __name__ == "__main__":
    main()
