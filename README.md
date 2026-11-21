# 三角洲弹药价格监控 · GitHub 部署说明

这套文件放到 GitHub 上之后：

- **采集**：GitHub 的服务器每 20 分钟自动在你仓库里跑一次采集（不用开你的电脑）；
- **展示**：同一份仓库用 GitHub Pages 挂成网页，手机用流量也能打开；
- **历史**：价格点位存在 `data/store.json` 里，一直累积（页面按小时/点位画 K 线）。

全部免费，不需要信用卡。

---

## 一、建仓库（约 2 分钟）

1. 打开 <https://github.com> ，登录（没有账号就先注册）。
2. 右上角 `+` → **New repository**。
3. 名字填 `ammo-price`（或任意英文名），可见性选 **Public**（免费额度只对公开仓库无限），
   下面的 “Add a README file” 等选项**都不要勾**。
4. 点 **Create repository**。

## 二、上传文件（约 3 分钟）

1. 把 `弹药监控-GitHub部署包.zip` **解压**（不要直接在压缩包里操作）。
2. 在新建的仓库页面点 **uploading an existing file**。
3. 把解压出来的这些东西**一起拖进去**（要包含 `.github` 文件夹和 `data` 文件夹）：
   - `index.html`
   - `ammo_monitor.py`
   - `build_static.py`
   - `data.json`
   - `data`（文件夹）
   - `.github`（文件夹）
4. 下面点 **Commit changes**。

> `.github` 是隐藏式点开头的文件夹，Windows 资源管理器能看到；
> 如果拖拽后没传上去，就在仓库里点 **Add file → Create new file**，文件名输入
> `.github/workflows/collect.yml`，再把文件内容粘进去。

## 三、开启网页（约 1 分钟）

1. 仓库 **Settings** → 左侧 **Pages**。
2. Source 选 **Deploy from a branch**，Branch 选 **main**、目录选 **/ (root)**，点 **Save**。
3. 等 1–2 分钟，页面顶部会出现网址，形如：
   `https://<你的用户名>.github.io/ammo-price/`
   这个就是手机随时能打开监控页的地址。

## 四、启用定时采集

1. 仓库顶部 **Actions** 标签。
2. 如果提示 “Workflows aren’t being run on this repository”，点 **I understand my workflows, go ahead and enable them**
   （定时任务需要手动启用一次）。
3. 点左侧 **ammo-collect** → 右侧 **Run workflow** 手动跑一次，确认变绿。
4. 之后每 20 分钟自动一轮，无需任何操作。

---

## 说明与限制（请照实理解）

- **不是每分钟**：GitHub 的定时任务最快 20 分钟一轮，实际可能再延迟几分钟。
- **仓库必须是 Public**：私有仓库的免费运行额度不够每天 72 轮。
- **长时间不动会停**：仓库 60 天完全没有提交，GitHub 会自动停掉定时任务；点一次 Run workflow 即可恢复。
- **数据口径**：页面显示的是云端最近一次采集的快照；每件弹药的 K 线用源站自带的点位 + 我们累积的点位合成，
  无更新的时段仍是“补平柱”。
- **只读**：这个仓库只存数据，不提供服务端接口；页面读的是同目录的 `data.json`。

## 文件说明

| 文件 | 作用 |
| --- | --- |
| `ammo_monitor.py` | 采集器，`--collect` 跑一轮，把结果并进 `data/store.json` |
| `build_static.py` | 把 `store.json` 导出成页面用的 `data.json`（3 天内全点位，更早按小时抽样，最多 30 天） |
| `index.html` | 监控页面（优先读 `data.json`；本地起服务时自动切实时） |
| `data/store.json` | 历史库，会随每轮采集增长 |
| `.github/workflows/collect.yml` | 定时任务：每 20 分钟采集 → 导出 → 提交 |
