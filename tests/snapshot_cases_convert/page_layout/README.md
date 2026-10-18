# DOCX 分页与页眉页脚转换案例

`layout.docx` 是实际转换输入，可直接用 Word 打开检查；
`snapshots-content/layout.md` 是对应的 Markdown 转换快照。
测试直接读取已提交的源文件，不会在运行时重新生成或覆盖它。

源文件包含五个节，覆盖连续、奇数页、偶数页和下一页分节，手动分页、
段落样式中的段前分页、页码起始值及编号格式，以及首页和奇偶页不同的页眉页脚。
页眉页脚中还包含格式文本、链接、图片、表格、公式和动态域。
正文中的 `Chapter 4` 使用“标题 1”样式，页眉的 `STYLEREF 1` 域引用该标题。
Word 更新域时会根据实际排版重新计算页码和总页数。

运行转换快照及 DOCX 重建检查：

```powershell
uv run pytest tests/test_convert_layout.py::test_convert_layout_snapshot_and_rebuild -q
```

检查并确认源文件的修改后，可在上述命令末尾加 `--snapshot-update` 更新 Markdown 快照。
测试会预先放入同名媒体文件，因此快照中图片路径的重命名同时验证转换不会覆盖已有图片。
