# GUI 文字修改指南

主要编辑 `src/pyocrpka/gui.py`，保存后重启程序即可。无需重新安装依赖。

| 想修改的地方 | 在文件中搜索 |
| --- | --- |
| 窗口标题栏 | `self.setWindowTitle` |
| 界面顶部大标题、副标题 | `title = QLabel`、`subtitle = QLabel` |
| 工单号、姓名的输入框示例 | `setPlaceholderText` |
| 填写区域标题 | `01  报销信息` |
| 上传区域标题、格式和选填说明 | `for key, text, kind, multi in` |
| 添加、删除按钮 | `class FileInput` 中的 `QPushButton` |
| 文件数量和拖动说明 | `已选`、`拖动可调整顺序` |
| OCR 明细表头 | `ITEM_COLUMNS` |
| 发票号、日期、价税合计等标签 | `for key, text in` |
| 表格上方提示 | `hint = QLabel` |
| 添加商品行、删除选中行 | `add_row`、`delete_row` |
| 确认框和下方说明 | `self.confirm`、`note = QLabel` |
| 开始识别、生成、打开文件夹 | `self.ocr_button`、`self.generate_button`、`self.open_button` |
| 运行中、成功、失败的状态文案 | `self.status.setText`、`self.set_status_error` |
| 字体、颜色、圆角、间距 | `STYLE` |
| 顶部头像、窗口图标 | 替换透明PNG `src/pyocrpka/assets/app-icon.png`；路径配置在 `paths.py` 的 `APP_ICON` |

例如修改标题：

```python
title = QLabel("PKAOCR")
```

例如修改表头，只改右侧中文，不改左侧英文键：

```python
("net_amount", "金额")
```

错误提示还分布在这些文件中：

- `src/pyocrpka/validation.py`：必填、文件格式、工单号等输入错误。
- `src/pyocrpka/models.py`：发票号、日期、数值和金额冲突提示。
- `src/pyocrpka/recognition.py`、`ocr.py`：识别错误。
- `src/pyocrpka/excel.py`、`output.py`：模板和文件生成错误。
- `src/pyocrpka/app.py`：应用名称、命令行帮助。

不要把 `PAYMENTS`、`CATEGORIES` 当作纯显示文案随意修改：它们也是校验和 Excel 写入值。不要修改英文数据键、模板表头或信号连接来调整界面措辞。

头像的逻辑尺寸在`gui.py`的`update_logo()`中设置，按`devicePixelRatioF()`生成对应物理像素。不要固定缩小成64个物理像素后再在Retina屏幕放大。
