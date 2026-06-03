#!/usr/bin/env python3
import os
import time
import logging
import urllib.parse
import urllib.request
import feedparser
import fitz  # PyMuPDF
import requests
import datetime
from pathlib import Path
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
SMTP_SERVER, SMTP_PORT = os.getenv("SMTP_SERVER"), int(os.getenv("SMTP_PORT", "587"))
SMTP_USERNAME, SMTP_PASSWORD = os.getenv("SMTP_USERNAME"), os.getenv("SMTP_PASSWORD")
EMAIL_FROM = os.getenv("EMAIL_FROM")
EMAIL_TO = [email.strip() for email in os.getenv("EMAIL_TO", "").split(",") if email.strip()]

PAPERS_DIR = Path("./papers")
PAPERS_DIR.mkdir(exist_ok=True)

# === 🎯 狙击目标配置 ===
# TARGET_QUERIES = [{"cat": "cs.CV", "conf": "CVPR"}, {"cat": "cs.AI", "conf": "NeurIPS"}]
TARGET_QUERIES = [
    {
        "cat": "cs.CV",                               # 领域：计算机视觉
        "confs": ["CVPR", "ICCV", "ECCV"],            # 视觉三大顶会打包
        "keywords": ["visual navigation", "(UAV OR drone OR quadrotor)"]      # 必须同时包含：视觉导航 AND 无人机
    },
    {
        "cat": "cs.RO",                               # 领域：机器人学
        "confs": ["ICRA", "IROS", "RSS"],             # 机器人三大顶会打包
        "keywords": ["visual navigation", "(UAV OR drone OR quadrotor)"]    # 必须同时包含：视觉导航 AND 无人机(drone)
    }
]
MAX_PAPERS = 5  # 每次精读篇数

def get_target_conference_papers():
    """精确检索包含【多个顶会标签】及特定关键词的最新论文"""
    all_papers = []
    
    for target in TARGET_QUERIES:
        cat = target["cat"]
        confs = target.get("confs", [])
        keywords = target.get("keywords", [])
        
        logger.info(f"正在狙击 {cat} 领域 -> 会议群 {confs} -> 关键词: {keywords}")
        
        # 1. 基础条件：分类 AND (会议A OR 会议B OR 会议C)
        if confs:
            conf_query = " OR ".join([f"co:{c}" for c in confs])
            raw_query = f"cat:{cat} AND ({conf_query})"
        else:
            raw_query = f"cat:{cat}"
        
        # 2. 附加条件：如果有关键词，拼接到查询语句中 (使用 AND 连接，要求同时满足)
        if keywords:
            kw_query = " AND ".join([f'all:"{kw}"' for kw in keywords])
            raw_query = f"({raw_query}) AND ({kw_query})"
            
        logger.info(f"生成的 ArXiv 底层查询语法: {raw_query}")
        
        # 将空格、引号等特殊字符转换为 URL 编码
        encoded_query = urllib.parse.quote(raw_query)
        url = f"http://export.arxiv.org/api/query?search_query={encoded_query}&sortBy=submittedDate&sortOrder=descending&max_results={MAX_PAPERS}"
        
        try:
            feed = feedparser.parse(url)
            if not feed.entries:
                logger.info(f"  -> 未找到符合当前苛刻条件的最新论文，跳过。")
                continue
                
            for entry in feed.entries:
                paper_info = {
                    "title": entry.title.replace('\n', ' '),
                    "authors": [author.name for author in entry.authors],
                    "comment": entry.get("arxiv_comment", "无备注"),
                    "pdf_url": entry.id.replace("/abs/", "/pdf/") + ".pdf",
                    "paper_id": entry.id.split('/')[-1]
                }
                all_papers.append(paper_info)
                logger.info(f"🎯 锁定目标: [{paper_info['comment']}] {paper_info['title']}")
        except Exception as e:
            logger.error(f"检索失败: {e}")
            
        time.sleep(3) # 礼貌延时，防止被 ArXiv 封禁
        
    return all_papers[:MAX_PAPERS]

def download_and_extract_text(paper_info):
    pdf_path = PAPERS_DIR / f"{paper_info['paper_id']}.pdf"
    logger.info(f"正在下载并提取 PDF: {paper_info['title']}")
    req = urllib.request.Request(paper_info['pdf_url'], headers={'User-Agent': 'Mozilla/5.0'})
    try:
        with urllib.request.urlopen(req) as response, open(pdf_path, 'wb') as out_file:
            out_file.write(response.read())
        doc = fitz.open(pdf_path)
        return " ".join([" ".join(page.get_text("text").split()) for page in doc]), pdf_path
    except Exception as e:
        logger.error(f"处理 PDF 失败: {e}")
        return None, pdf_path

def deep_analyze_with_ai(paper, full_text):
    prompt = f"""
    论文标题: {paper['title']}
    作者: {', '.join(paper['authors'])}
    会议标签: {paper['comment']}
    
    你现在是一名深耕专业领域的文献研读与技术拆解达人。请仔细阅读以下论文全文，并输出一份高价值的核心技术拆解报告。
    
    ### 1. 研究前沿与痛点锚定
    - 本文试图解决的关键技术瓶颈是什么？
    ### 2. 核心技术原理与创新设计
    - **算法/架构拆解**：精准提炼其核心技术原理和创新设计。
    ### 3. 技术优劣势与纵向对比
    - **优势与突破**：相比已有基线模型，其技术优势在哪里？
    ### 4. 落地适配度与高价值参考
    - 提炼出文献中可落地的技术要点或未来启发。
    
    【论文全文内容】：
    {full_text[:50000]}
    """
    payload = {
        "model": "deepseek-v4-pro",
        "messages": [{"role": "system", "content": "你是一位顶级的学术论文拆解专家。"}, {"role": "user", "content": prompt}],
        "temperature": 0.2
    }
    try:
        response = requests.post("https://api.deepseek.com/chat/completions", headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}"}, json=payload, timeout=120)
        return response.json()['choices'][0]['message']['content'] if response.status_code == 200 else f"API 错误: {response.text}"
    except Exception as e:
        return f"网络错误: {e}"

def main():
    papers = get_target_conference_papers()
    if not papers: return
        
    analyses, content = [], f"## 🏆 顶会论文精读报告 ({datetime.datetime.now().strftime('%Y-%m-%d')})\n\n"
    for paper in papers:
        full_text, pdf_path = download_and_extract_text(paper)
        if full_text:
            analysis = deep_analyze_with_ai(paper, full_text)
            content += f"### [{paper['comment']}] {paper['title']}\n**链接**: {paper['pdf_url']}\n\n{analysis}\n\n---\n\n"
        if pdf_path and pdf_path.exists(): pdf_path.unlink() # 阅后即焚
            
    if EMAIL_TO:
        msg = MIMEMultipart()
        msg['From'], msg['To'], msg['Subject'] = EMAIL_FROM, ", ".join(EMAIL_TO), f"🏆 顶会精读报告 - {datetime.datetime.now().strftime('%Y-%m-%d')}"
        msg.attach(MIMEText(f"<html><body>{content.replace(chr(10), '<br>')}</body></html>", 'html'))
        try:
            with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
                server.starttls()
                server.login(SMTP_USERNAME, SMTP_PASSWORD)
                server.send_message(msg)
            logger.info("精读邮件发送成功！")
        except Exception as e:
            logger.error(f"发邮件失败: {e}")

if __name__ == "__main__":
    main()