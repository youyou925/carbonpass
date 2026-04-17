# 票据识别系统 (Receipt OCR)

基于 Python Flask + Tesseract OCR 的票据识别工具，可以自动识别发票、收据、账单上的文字信息。

## 功能特点

- 上传图片自动识别文字
- 支持多种图片格式 (JPG, PNG, GIF, BMP)
- 中英文混合识别
- 一键复制识别结果
- 友好的Web界面

## 环境要求

- Python 3.8+
- Tesseract OCR 引擎

## 安装步骤

### 1. 安装 Python 依赖

```bash
cd receipt-ocr
pip install -r requirements.txt
```

### 2. 安装 Tesseract OCR

**Windows 用户：**
1. 下载地址：https://github.com/UB-Mannheim/tesseract/wiki
2. 安装时选择中文语言包
3. 安装完成后，修改 `app.py` 中的路径：
```python
pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'
```
将路径改为你实际的安装路径。

**Linux 用户：**
```bash
sudo apt-get install tesseract-ocr
sudo apt-get install tesseract-ocr-chi-sim  # 中文简体语言包
```

**Mac 用户：**
```bash
brew install tesseract
brew install tesseract-lang  # 包含中文
```

## 运行

```bash
python app.py
```

终端会显示：
```
==================================================
票据识别系统已启动
请在浏览器中打开: http://127.0.0.1:5000
按 Ctrl+C 停止服务器
==================================================
```

打开浏览器访问 `http://127.0.0.1:5000` 即可使用。

## 使用方法

1. 打开网页，点击上传区域或拖拽图片
2. 选择一张票据/发票/收据图片
3. 点击"开始识别"按钮
4. 等待几秒后查看识别结果
5. 点击"复制结果"按钮复制到剪贴板

## 识别效果说明

- 印刷清晰的票据：识别率约 80-90%
- 手写票据：识别率约 50-70%
- 模糊或倾斜的图片：识别率会下降

**提高识别率的方法：**
- 使用清晰、照明均匀的图片
- 尽量让文字水平放置
- 分辨率越高越好（建议 300 DPI 以上）

## 配合碳核算使用

识别结果可用于：
1. 提取发票中的电费、燃油费等能耗数据
2. 计算企业碳排放量
3. 生成碳核算报告

## 文件结构

```
receipt-ocr/
├── app.py              # Flask 主程序
├── templates/
│   └── index.html      # 网页前端
├── uploads/            # 图片上传目录（自动创建）
├── requirements.txt    # Python 依赖
└── README.md           # 使用说明
```

## 常见问题

**Q: 提示 "Tesseract not found"**
A: Windows 用户需要修改 `app.py` 中的 `tesseract_cmd` 路径，指向你安装的 Tesseract 可执行文件路径。

**Q: 识别结果是乱码**
A: 请确保安装了中文语言包。Linux 用户运行 `sudo apt-get install tesseract-ocr-chi-sim`

**Q: 识别率很低**
A: 检查图片是否清晰，确保安装了中文语言包，尝试旋转图片后重新识别。

## 免责声明

本工具基于开源 Tesseract OCR 引擎，识别率受图片质量和环境影响，不保证100%准确。建议结合人工校对使用。