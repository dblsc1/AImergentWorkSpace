"""窗口块「看得懂」的内置默认数据（契约 v2.46）：网站域名、站点名 → 域名、程序 → 程序家族。只放数据，改名单只改这一个文件；只增。"""

from __future__ import annotations

# 内置域名：与用户的 browserDomains 并用；每条同时覆盖它的子域名。
DEFAULT_DOMAINS = frozenset("""
github.com gitlab.com bitbucket.org gitee.com stackoverflow.com stackexchange.com superuser.com serverfault.com askubuntu.com
docs.python.org python.org developer.mozilla.org npmjs.com pypi.org crates.io docs.rs go.dev pkg.go.dev readthedocs.io readthedocs.org
rust-lang.org kotlinlang.org swift.org developer.apple.com developer.android.com learn.microsoft.com docs.microsoft.com
nodejs.org reactjs.org react.dev vuejs.org angular.io svelte.dev nextjs.org tailwindcss.com typescriptlang.org
docker.com hub.docker.com kubernetes.io aws.amazon.com cloud.google.com azure.microsoft.com vercel.com netlify.com cloudflare.com
huggingface.co kaggle.com leetcode.com leetcode.cn codepen.io jsfiddle.net replit.com codesandbox.io regex101.com
dev.to medium.com hashnode.com infoq.com news.ycombinator.com lobste.rs
claude.ai anthropic.com chatgpt.com openai.com platform.openai.com gemini.google.com aistudio.google.com copilot.microsoft.com
perplexity.ai poe.com grok.com x.ai chat.deepseek.com deepseek.com kimi.com kimi.moonshot.cn moonshot.cn doubao.com tongyi.aliyun.com
qianwen.com yiyan.baidu.com chatglm.cn chat.z.ai z.ai yuanbao.tencent.com minimax.io hailuoai.com mistral.ai midjourney.com
notion.so notion.site figma.com linear.app slack.com trello.com asana.com atlassian.net atlassian.com
airtable.com miro.com canva.com clickup.com monday.com feishu.cn larksuite.com larkoffice.com dingtalk.com teams.microsoft.com
docs.google.com drive.google.com sheets.google.com slides.google.com calendar.google.com mail.google.com meet.google.com
office.com office365.com microsoft365.com outlook.com outlook.live.com onedrive.live.com sharepoint.com zoom.us
yuque.com shimo.im wolai.com mubu.com processon.com kdocs.cn docs.qq.com wps.cn tapd.cn
bilibili.com zhihu.com baidu.com tieba.baidu.com juejin.cn csdn.net cnblogs.com segmentfault.com jianshu.com oschina.net
weibo.com douyin.com xiaohongshu.com taobao.com tmall.com jd.com pinduoduo.com 1688.com alipay.com
weixin.qq.com mp.weixin.qq.com qq.com 163.com sina.com.cn sohu.com douban.com v2ex.com ithome.com 36kr.com sspai.com
ke.com meituan.com ele.me dianping.com ctrip.com trip.com 12306.cn amap.com map.baidu.com
youtube.com youtu.be netflix.com twitch.tv vimeo.com spotify.com music.163.com y.qq.com iqiyi.com youku.com v.qq.com
twitter.com x.com facebook.com instagram.com reddit.com linkedin.com discord.com telegram.org web.telegram.org web.whatsapp.com
tiktok.com pinterest.com quora.com
amazon.com ebay.com aliexpress.com etsy.com shopify.com paypal.com
wikipedia.org wikimedia.org arxiv.org scholar.google.com cnki.net deepl.com translate.google.com fanyi.baidu.com google.com bing.com
duckduckgo.com sogou.com so.com
""".split())

# 标题末尾常是站点名而不是域名：casefold 的站点名 → 域名。
SITE_NAMES = {k.casefold(): v for k, v in {
    "YouTube": "youtube.com", "知乎": "zhihu.com", "Notion": "notion.so", "哔哩哔哩": "bilibili.com", "bilibili": "bilibili.com",
    "GitHub": "github.com", "GitLab": "gitlab.com", "Stack Overflow": "stackoverflow.com",
    "百度": "baidu.com", "百度一下": "baidu.com", "百度搜索": "baidu.com", "掘金": "juejin.cn", "稀土掘金": "juejin.cn", "CSDN博客": "csdn.net",
    "CSDN": "csdn.net", "博客园": "cnblogs.com", "SegmentFault 思否": "segmentfault.com", "简书": "jianshu.com", "微博": "weibo.com",
    "新浪微博": "weibo.com", "抖音": "douyin.com", "小红书": "xiaohongshu.com", "淘宝网": "taobao.com", "淘宝": "taobao.com",
    "天猫": "tmall.com", "京东": "jd.com", "京东(JD.COM)": "jd.com", "拼多多": "pinduoduo.com", "豆瓣": "douban.com",
    "Claude": "claude.ai", "ChatGPT": "chatgpt.com", "Gemini": "gemini.google.com", "DeepSeek": "deepseek.com", "Kimi": "kimi.com",
    "豆包": "doubao.com", "通义": "qianwen.com", "Perplexity": "perplexity.ai", "Figma": "figma.com", "Linear": "linear.app",
    "Slack": "slack.com", "飞书云文档": "feishu.cn", "飞书": "feishu.cn", "Lark": "larksuite.com", "钉钉": "dingtalk.com",
    "语雀": "yuque.com", "石墨文档": "shimo.im", "腾讯文档": "docs.qq.com", "金山文档": "kdocs.cn",
    "Google Docs": "docs.google.com", "Google Sheets": "sheets.google.com", "Google Slides": "slides.google.com",
    "Google Drive": "drive.google.com", "Google Calendar": "calendar.google.com", "Gmail": "mail.google.com", "Google": "google.com",
    "Google Search": "google.com", "Bing": "bing.com", "Microsoft Bing": "bing.com", "DuckDuckGo": "duckduckgo.com",
    "Wikipedia": "wikipedia.org", "维基百科": "wikipedia.org", "Reddit": "reddit.com", "Twitter": "twitter.com", "X": "x.com",
    "Facebook": "facebook.com", "Instagram": "instagram.com", "LinkedIn": "linkedin.com", "Medium": "medium.com", "Hacker News": "news.ycombinator.com",
    "Amazon.com": "amazon.com", "Netflix": "netflix.com", "Twitch": "twitch.tv", "Spotify": "spotify.com", "Discord": "discord.com",
    "npm": "npmjs.com", "PyPI": "pypi.org", "MDN Web Docs": "developer.mozilla.org", "MDN": "developer.mozilla.org",
    "Read the Docs": "readthedocs.io", "Docker Hub": "hub.docker.com", "Hugging Face": "huggingface.co", "LeetCode": "leetcode.com",
    "力扣": "leetcode.cn", "力扣（LeetCode）": "leetcode.cn", "Outlook": "outlook.com", "Microsoft Teams": "teams.microsoft.com",
    "Zoom": "zoom.us", "网易云音乐": "music.163.com", "QQ音乐": "y.qq.com", "爱奇艺": "iqiyi.com",
    "优酷": "youku.com", "微信公众平台": "mp.weixin.qq.com", "36氪": "36kr.com", "少数派": "sspai.com", "V2EX": "v2ex.com",
}.items()}

# 程序 / 应用标识（casefold，去掉 .exe）→ 程序家族名；一个值下的标识用空格分开。浏览器与终端的名单在 block_display（契约附录）。
APP_FAMILIES = {k.casefold(): v for v, ks in {
    "VS Code": "code code-oss vscodium codium code-insiders",
    "Cursor": "cursor", "Windsurf": "windsurf", "Zed": "zed zed-editor dev.zed.zed", "Sublime Text": "sublime_text sublime-text subl",
    "JetBrains": "idea idea64 pycharm pycharm64 webstorm webstorm64 goland goland64 clion clion64 rider rider64 phpstorm phpstorm64 "
                 "rubymine datagrip android-studio studio64 fleet jetbrains-idea jetbrains-pycharm jetbrains-webstorm jetbrains-goland "
                 "jetbrains-clion jetbrains-rider jetbrains-phpstorm jetbrains-rubymine jetbrains-datagrip jetbrains-studio",
    "Vim": "vim nvim neovim gvim emacs", "微信": "wechat weixin 微信 wechatappex", "QQ": "qq qqnt", "飞书": "feishu lark",
    "钉钉": "dingtalk 钉钉", "Slack": "slack", "Telegram": "telegram telegram-desktop telegramdesktop org.telegram.desktop",
    "Discord": "discord", "Obsidian": "obsidian md.obsidian.obsidian", "Notion": "notion notion-app", "Typora": "typora", "Logseq": "logseq",
    "Word": "winword", "Excel": "excel", "PowerPoint": "powerpnt",
    "WPS": "wps wpsoffice et wpp", "LibreOffice": "soffice libreoffice libreoffice-writer libreoffice-calc",
    "Figma": "figma", "Postman": "postman", "Zoom": "zoom zoom.us", "腾讯会议": "wemeet wemeetapp",
}.items() for k in ks.split()}

# 编辑器标题末尾的编辑器名（「文件 — 工作区 — 编辑器」）；JetBrains 是「项目 – 文件」，项目在前。
EDITOR_SUFFIXES = {s.casefold() for s in ("Visual Studio Code", "Code - OSS", "VSCodium", "Cursor", "Windsurf", "Zed", "Sublime Text")}
PROJECT_FIRST_FAMILIES = {"JetBrains"}
