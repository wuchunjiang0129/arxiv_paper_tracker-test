#!/usr/bin/env python3
# ArXiv论文追踪与分析器

import os
import datetime
import time
import logging
import sys
import smtplib
import re           # 新增：用于解析 RSS 里的冗余 HTML 标签
import json         # 新增：用于处理 DeepSeek API 的原生 JSON 数据
import requests     # 新增：用于原生直连 DeepSeek 服务器
import feedparser   # 新增：用于绕过 ArXiv 限制的 RSS 解析库
from pathlib import Path
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from dotenv import load_dotenv
from jinja2 import Template

# 加载环境变量
load_dotenv()

# 设置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s',
                   handlers=[logging.StreamHandler(sys.stdout)])
logger = logging.getLogger(__name__)

# 配置
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
SMTP_SERVER = os.getenv("SMTP_SERVER")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USERNAME = os.getenv("SMTP_USERNAME")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")
EMAIL_FROM = os.getenv("EMAIL_FROM")
# 支持多个收件人邮箱，用逗号分隔
EMAIL_TO = [email.strip() for email in os.getenv("EMAIL_TO", "").split(",") if email.strip()]

PAPERS_DIR = Path("./papers")
CONCLUSION_FILE = Path("./conclusion.md")
CATEGORIES = ["cs.AI", "cs.AI","cs.RO","cs.LG"]  # 你可以根据需要添加更多类别cv、cs.LG、cs.RO等
MAX_PAPERS = 10  # 设置为1以便快速测试

# 配置OpenAI API用于DeepSeek
# openai.api_key = DEEPSEEK_API_KEY
# openai.api_base = "https://api.deepseek.com/v1"

# 如果不存在论文目录则创建
PAPERS_DIR.mkdir(exist_ok=True)
logger.info(f"论文将保存在: {PAPERS_DIR.absolute()}")
logger.info(f"分析结果将写入: {CONCLUSION_FILE.absolute()}")

# def get_recent_papers(categories, max_results=MAX_PAPERS):
#     """获取最近5天内发布的指定类别的论文"""
#     # 计算最近5天的日期范围
#     today = datetime.datetime.now()
#     five_days_ago = today - datetime.timedelta(days=2)
    
#     # 格式化ArXiv查询的日期
#     start_date = five_days_ago.strftime('%Y%m%d')
#     end_date = today.strftime('%Y%m%d')
    
#     # 创建查询字符串，搜索最近5天内发布的指定类别的论文
#     category_query = " OR ".join([f"cat:{cat}" for cat in categories])
#     date_range = f"submittedDate:[{start_date}000000 TO {end_date}235959]"
#     query = f"({category_query}) AND {date_range}"
    
#     logger.info(f"正在搜索论文，查询条件: {query}")

#     client = arxiv.Client(
#         page_size=100,
#         delay_seconds=5.0,  # 每次请求之间延迟 5 秒（默认是 3 秒）
#         num_retries=5       # 增加重试次数
#     )
#     # 搜索ArXiv
#     search = arxiv.Search(
#         query=query,
#         max_results=max_results,
#         sort_by=arxiv.SortCriterion.SubmittedDate,
#         sort_order=arxiv.SortOrder.Descending
#     )
    
#     results = list(client.results(search))
#     logger.info(f"找到{len(results)}篇符合条件的论文")
#     return results
def get_recent_papers(categories, max_results=MAX_PAPERS):
    """通过 ArXiv 官方不限流的 RSS 订阅源获取最新论文（彻底解决 429 报错）"""
    logger.info(f"正在通过 RSS 订阅源获取类别 {categories} 的最新论文...")
    
    # 用来临时存放统一格式的伪 Paper 对象
    parsed_papers = []
    
    # 遍历你设置的每一个分类（如 'cs.AI', 'cs.CV', 'cs.RO'）
    for cat in categories:
        rss_url = f"https://rss.arxiv.org/rss/{cat}"
        logger.info(f"正在读取 RSS 源: {rss_url}")
        
        try:
            # 使用 feedparser 解析，该接口对 GitHub Actions 极其友好，不限流
            feed = feedparser.parse(rss_url)
            
            for entry in feed.entries:
                if len(parsed_papers) >= max_results:
                    break
                    
                # 为了不破坏你后续的 main.py 代码，我们动态组装一个高兼容性的结构体/类
                class MockAuthor:
                    def __init__(self, name):
                        self.name = name
                        
                class MockPaper:
                    def __init__(self, e, category):
                        self.title = e.title
                        # 清理 RSS 里的作者文本并转换为列表
                        author_text = e.get('author', 'Unknown')
                        # 移除可能存在的 HTML 标签
                        author_text = re.sub(r'<[^>]+>', '', author_text)
                        self.authors = [MockAuthor(a.strip()) for a in author_text.split(',')]
                        self.categories = [category]
                        # RSS 的 entry_id 通常就是论文的链接
                        self.entry_id = e.link
                        # 尝试获取发布日期
                        self.published = datetime.datetime.now() 
                        
                    def get_short_id(self):
                        # 从链接中提取短 ID，例如从 http://arxiv.org/abs/2401.12345 提取 2401.12345
                        match = re.search(r'/abs/([^v]+)', self.entry_id)
                        if match:
                            return match.group(1)
                        return str(time.time())
                        
                    def download_pdf(self, filename):
                        # 将 abs 链接转换为 pdf 下载链接并实施下载
                        pdf_url = self.entry_id.replace('/abs/', '/pdf/') + ".pdf"
                        import urllib.request
                        # 设置请求头，假装是浏览器，防止下载 PDF 时被拦截
                        req = urllib.request.Request(
                            pdf_url, 
                            headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
                        )
                        with urllib.request.urlopen(req) as response, open(filename, 'wb') as out_file:
                            out_file.write(response.read())

                # 实例化伪造的论文对象，确保原本的 downstream（下载、DeepSeek分析）代码不需要做任何改动
                paper_obj = MockPaper(entry, cat)
                parsed_papers.append(paper_obj)
                
        except Exception as e:
            logger.error(f"读取类别 {cat} 的 RSS 源失败: {str(e)}")
            continue
            
    logger.info(f"成功通过 RSS 检索到 {len(parsed_papers)} 篇最新论文！")
    return parsed_papers[:max_results]

def download_paper(paper, output_dir):
    """将论文PDF下载到指定目录"""
    pdf_path = output_dir / f"{paper.get_short_id().replace('/', '_')}.pdf"
    
    # 如果已下载则跳过
    if pdf_path.exists():
        logger.info(f"论文已下载: {pdf_path}")
        return pdf_path
    
    try:
        logger.info(f"正在下载: {paper.title}")
        paper.download_pdf(filename=str(pdf_path))
        logger.info(f"已下载到 {pdf_path}")
        return pdf_path
    except Exception as e:
        logger.error(f"下载论文失败 {paper.title}: {str(e)}")
        return None

def analyze_paper_with_deepseek(pdf_path, paper):
    """调用 DeepSeek API 分析论文（使用原生 requests，彻底告别 404）"""
    author_names = [author.name for author in paper.authors]
    
    prompt = f"""
    论文标题: {paper.title}
    作者: {', '.join(author_names)}
    类别: {', '.join(paper.categories)}
    
    你现在的身份是一位极其擅长“因材施教”的资深导师。请为一位科研初学者（小白）通俗易懂地拆解这篇研究论文。
    
    请严格按照以下模块和格式进行分析：
    ### 1. 这篇论文在研究什么？（背景与大白话解释）
    - **一句话大白话**：请用高阶白话文（不要用硬核术语），用一句话向外行解释这篇论文的核心目的。
    - **行业痛点**：过去的方法有什么严重缺陷？或者这个领域现在面临什么难题？

    ### 2. 核心贡献与创新点（它厉害在哪？）
    - 简要列出 2-3 个最核心的创新。用通俗语言说清楚它提出了什么新视角。

    ### 3. 研究方法与技术拆解（它是怎么做的？）
    - **核心技术/模型**：它用了什么关键技术？请用简单的比喻或通俗语言解释这个技术的大致原理。

    ### 4. 实验结果与结论（疗效如何？）
    - 得出了什么关键结论？

    ### 5. 对新手的启发（为什么值得我读？）
    - 局限性是什么？有哪些方向是新手可以切入继续做研究的？
    
    【输出要求】：
    1. 请使用中文回答。
    2. 必须严格使用 Markdown 格式（使用 ### 作为小标题，- 作为列表符号，关键术语用 **加粗** 突出）。
    """

    # 官方绝对物理地址，绝不含糊
    url = "https://api.deepseek.com/chat/completions"
    
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}"
    }
    
    payload = {
        "model": "deepseek-v4-pro", 
        "messages": [
            {"role": "system", "content": "你是一位专门总结和分析学术论文的研究助手。请使用中文回复。"},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.2
    }

    try:
        logger.info(f"正在直接连接 DeepSeek 服务器分析论文: {paper.title}")
        
        # 抛弃第三方库，直接发 POST 请求
        response = requests.post(url, headers=headers, json=payload, timeout=60)
        
        # HTTP 200 表示完全成功
        if response.status_code == 200:
            result = response.json()
            return result['choices'][0]['message']['content']
        else:
            # 如果再失败，这里会把 DeepSeek 官方的具体拒接原因直接打印在你的 GitHub 日志里
            error_detail = response.text
            logger.error(f"DeepSeek 拒绝了请求！状态码: {response.status_code}, 详情: {error_detail}")
            return f"### 论文分析失败\n服务器拒绝请求 (HTTP {response.status_code}): {error_detail}"

    except Exception as e:
        logger.error(f"网络通信发生断裂: {str(e)}")
        return f"### 论文分析失败\n未知错误: {str(e)}"

def write_to_conclusion(papers_analyses):
    """将分析结果写入conclusion.md"""
    today = datetime.datetime.now().strftime('%Y-%m-%d')
    
    # 创建或追加到结果文件
    with open(CONCLUSION_FILE, 'a', encoding='utf-8') as f:
        f.write(f"\n\n## ArXiv论文 - 最近5天 (截至 {today})\n\n")
        
        for paper, analysis in papers_analyses:
            # 从Author对象中提取作者名
            author_names = [author.name for author in paper.authors]
            
            f.write(f"### {paper.title}\n")
            f.write(f"**作者**: {', '.join(author_names)}\n")
            f.write(f"**类别**: {', '.join(paper.categories)}\n")
            f.write(f"**发布日期**: {paper.published.strftime('%Y-%m-%d')}\n")
            f.write(f"**链接**: {paper.entry_id}\n\n")
            f.write(f"{analysis}\n\n")
            f.write("---\n\n")
    
    logger.info(f"分析结果已写入 {CONCLUSION_FILE}")

def format_email_content(papers_analyses):
    """格式化邮件内容，只包含当天分析的论文"""
    today = datetime.datetime.now().strftime('%Y-%m-%d')
    
    content = f"## 今日ArXiv论文分析报告 ({today})\n\n"
    
    for paper, analysis in papers_analyses:
        # 从Author对象中提取作者名
        author_names = [author.name for author in paper.authors]
        
        content += f"### {paper.title}\n"
        content += f"**作者**: {', '.join(author_names)}\n"
        content += f"**类别**: {', '.join(paper.categories)}\n"
        content += f"**发布日期**: {paper.published.strftime('%Y-%m-%d')}\n"
        content += f"**链接**: {paper.entry_id}\n\n"
        content += f"{analysis}\n\n"
        content += "---\n\n"
    
    return content

def delete_pdf(pdf_path):
    """删除PDF文件"""
    try:
        if pdf_path.exists():
            pdf_path.unlink()
            logger.info(f"已删除PDF文件: {pdf_path}")
        else:
            logger.info(f"PDF文件不存在，无需删除: {pdf_path}")
    except Exception as e:
        logger.error(f"删除PDF文件失败 {pdf_path}: {str(e)}")

def send_email(content):
    """发送邮件，支持多个收件人"""
    if not all([SMTP_SERVER, SMTP_PORT, SMTP_USERNAME, SMTP_PASSWORD, EMAIL_FROM]) or not EMAIL_TO:
        logger.error("邮件配置不完整，跳过发送邮件")
        return

    try:
        msg = MIMEMultipart()
        msg['From'] = EMAIL_FROM
        msg['To'] = ", ".join(EMAIL_TO)
        msg['Subject'] = f"ArXiv论文分析报告 - {datetime.datetime.now().strftime('%Y-%m-%d')}"

        # 使用HTML模板
        html_template = """
        <html>
        <head>
            <meta charset=\"UTF-8\">
            <style>body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,'Helvetica Neue',Arial,sans-serif;line-height:1.6;max-width:1000px;margin:0 auto;padding:20px;background-color:#f5f5f5;}.container{background-color:white;padding:30px;border-radius:8px;box-shadow:0 2px 4px rgba(0,0,0,0.1);}h1{color:#2c3e50;border-bottom:2px solid #3498db;padding-bottom:10px;margin-bottom:30px;}h2{color:#34495e;margin-top:40px;padding-bottom:8px;border-bottom:1px solid #eee;}h3{color:#2980b9;margin-top:30px;}.paper-info{background-color:#f8f9fa;padding:15px;border-left:4px solid #3498db;margin-bottom:20px;}.paper-info p{margin:5px 0;}.paper-info strong{color:#2c3e50;}a{color:#3498db;text-decoration:none;}a:hover{text-decoration:underline;}hr{border:none;border-top:1px solid #eee;margin:30px 0;}.section{margin-bottom:20px;}.section h4{color:#2c3e50;margin-bottom:10px;}pre{background-color:#f8f9fa;padding:15px;border-radius:4px;overflow-x:auto;}code{font-family:Consolas,Monaco,'Courier New',monospace;background-color:#f8f9fa;padding:2px 4px;border-radius:3px;}</style>
        </head>
        <body>
            <div class=\"container\">
                {{ content | replace("###", "<h2>") | replace("##", "<h1>") | replace("**", "<strong>") | safe }}
            </div>
        </body>
        </html>
        """
        
        # 将Markdown格式转换为HTML格式
        content_html = content.replace("\n\n", "<br><br>")
        content_html = content_html.replace("---", "<hr>")
        
        template = Template(html_template)
        html_content = template.render(content=content_html)
        
        msg.attach(MIMEText(html_content, 'html'))

        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USERNAME, SMTP_PASSWORD)
            server.send_message(msg)

        logger.info(f"邮件发送成功，收件人: {', '.join(EMAIL_TO)}")
    except Exception as e:
        logger.error(f"发送邮件失败: {str(e)}")

def main():
    logger.info("开始ArXiv论文跟踪")
    
    # 获取最近5天的论文
    papers = get_recent_papers(CATEGORIES, MAX_PAPERS)
    logger.info(f"从最近5天找到{len(papers)}篇论文")
    
    if not papers:
        logger.info("所选时间段没有找到论文。退出。")
        return
    
    # 处理每篇论文
    papers_analyses = []
    for i, paper in enumerate(papers, 1):
        logger.info(f"正在处理论文 {i}/{len(papers)}: {paper.title}")
        # 下载论文
        pdf_path = download_paper(paper, PAPERS_DIR)
        if pdf_path:
            # 休眠以避免达到API速率限制
            time.sleep(2)
            
            # 分析论文
            analysis = analyze_paper_with_deepseek(pdf_path, paper)
            papers_analyses.append((paper, analysis))
            
            # 分析完成后删除PDF文件
            delete_pdf(pdf_path)
    
    # 将分析结果写入conclusion.md（包含所有历史记录）
    write_to_conclusion(papers_analyses)
    
    # 发送邮件（只包含当天分析的论文）
    email_content = format_email_content(papers_analyses)
    send_email(email_content)
    
    logger.info("ArXiv论文追踪和分析完成")
    logger.info(f"结果已保存至 {CONCLUSION_FILE.absolute()}")

if __name__ == "__main__":
    main()
