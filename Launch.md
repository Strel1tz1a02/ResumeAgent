- 启动后端：
```bash
cd apps/backend
conda activate resume-matcher
python -m app.main
```
或
```bash
cd apps/backend
conda activate resume-matcher
python -m app.main
uvicorn app.main:app --reload --port 8000
```
- 启动前端：
```bash
cd apps/frontend
npm run dev
```