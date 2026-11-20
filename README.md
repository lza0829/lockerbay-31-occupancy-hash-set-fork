# 格湾（LockerBay）

**骨架：** 入柜/取件事件装载 → 格口占用物化 → 超时回收 → 占用账对账

- 领域：数据工程（去皮=事件装载 + 占用状态物化 + 超时闸/回收 + 占用核证）
- 端口：前端 **6100**，接口 **11100**（避开 Chrome 封锁的 6000–6063）
- 公有仓前缀：`lza0829/lockerbay-NN-*`
- 非仓库库存 CRUD、非运单轨迹轴、非营业日封账、非会话窗切分

本地冒烟：

```powershell
cd claude-workspace/lockerbay/_scaffold/backend
pip install -r requirements.txt
$env:DATA_DIR="$PWD/data"; $env:FIXTURES_DIR="$PWD/../fixtures"
python -m uvicorn app.main:app --host 127.0.0.1 --port 11100

cd ../frontend
npm install
npm run dev
```
