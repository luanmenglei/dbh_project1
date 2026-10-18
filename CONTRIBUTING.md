# 复现、反馈与贡献

[项目首页](README.md) · [完整复现指南](docs/复现指南.md) · [原始证据](results/README.md)

这个仓库保存一份数据库课程实验及其可追溯记录。欢迎修正文档、报告复现问题，或增加具有明确实验条件的新对照。

## 报告问题

在 [Issues](https://github.com/luanmenglei/dbh_project1/issues) 中提供：

- 使用的提交版本（`git rev-parse HEAD`）、操作系统、编译器和数据库版本。
- 从仓库根目录执行的命令、预期结果与实际结果。
- 相关日志和数据规模；连接密码、令牌及完整连接字符串请先移除。
- 若反馈报告数字，注明页码、图表编号和对应实验目录。

## 修改代码与文档

保持一次修改围绕一个问题，并说明改变的行为及验证方式。文档中的运行命令应指向实际存在的脚本；展示结果应能追溯到已保存的记录。

```bash
# 运行器、清理逻辑及证据审查回归，不启动数据库
python3 -m unittest discover -s tests -p 'test_*.py'

# Ubuntu/Linux 完整集成检查，需 C++17、libpq 和 PostgreSQL 服务端工具
make test
```

不要把两条命令的通过范围混为一谈。第二条会创建临时 PostgreSQL 集群；openGauss 的验证需要其配套服务与 libpq，见复现指南。

## 增加实验

新实验应使用新的结果目录，并记录源码身份、输入、计时边界、重复次数、预热方法、CPU 绑定和资源状态。保留逐轮结果、失败及排除原因。

旧批次的原始输出和源码快照保留原样。不同计时版本、硬件或资源配置分别汇总；新增性能结论时同时给出波动范围与适用条件。

`results/` 默认忽略新目录。准备发布时先审查完整性和连接信息，再在 `.gitignore` 中明确允许该批目录。

## 更新报告与首页预览

```bash
python3 -m pip install -r requirements-plots.txt
python3 scripts/build_typst_report.py --font 'Noto Sans CJK SC'
python3 scripts/build_readme_assets.py
```

报告构建需要 Typst、指定字体和已有实验记录；预览生成需要 Poppler 的 `pdftoppm`。Windows 默认字体为 Microsoft YaHei，可省略 `--font`。检查 PDF 逐页排版后，一并提交源稿、图表、PDF、哈希清单和预览图。
