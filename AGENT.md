## Text encoding

- 中文文件默认按 UTF-8 读取。
- 终端中出现乱码不能作为文件损坏的证据。
- 在声称文件乱码前，必须使用严格 UTF-8 解码验证原始字节，并检查 Git diff。
- Windows 下读取文本时必须显式指定编码，例如 `Get-Content -Raw -Encoding UTF8`。
- 禁止仅因为终端显示异常而重写文件或批量转换编码。
- 如果文件不是 UTF-8，先识别其实际编码并报告，不得擅自转换。

## Development workflow

- 小型修改直接进行，不走 Superpowers 流程，不强制写测试。
- 只有大型修改才使用 Superpowers 流程。

## 开发习惯
- 名称应当精简，语义清晰
- 函数应当注释：参数含义，函数功能
- 中文为第一语言，而不是英语，注释、prompt、文档应当使用中文