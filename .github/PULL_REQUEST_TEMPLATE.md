## 变更说明

<!-- 说明改动的动机、解决的问题和主要实现。 -->

## 关联 Issue

<!-- 例如：Closes #123。没有关联 Issue 时请说明原因。 -->

## 验证方式

<!-- 写出评审者可以复现的命令和操作步骤。勾选本次改动适用的项目。 -->

- [ ] 后端：`cd apps/backend && uv run pytest`
- [ ] 前端 lint：`cd apps/frontend && npm run lint`
- [ ] 前端测试：`cd apps/frontend && npm run test`
- [ ] 前端构建：`cd apps/frontend && npm run build`
- [ ] 多语言：`python scripts/check_locale_parity.py`
- [ ] 其他验证（请在下方说明）

## 界面变化

<!-- 有界面变化时附前后截图或录屏；没有则写“无”。 -->

## 提交前检查

- [ ] 改动范围聚焦，未混入无关格式化或生成文件
- [ ] 已补充或更新必要的测试与文档
- [ ] 未提交 API Key、`.env`、个人数据或本地数据库
- [ ] 新增/修改的用户文案已同步所有语言文件
