# release/ · 发布包

小白路线（不 clone 源码、只装 Docker）用到的全部东西都在这里：

| 文件 | 作用 |
|---|---|
| `web.Dockerfile` | 网关镜像：nginx + 网关模板 + 顶栏 + 登录页 + hive / ring，文件与源码版挂载的是同一批 |
| `auth.Dockerfile` | 占位认证服务镜像（`contracts/auth.gate.v1` 的参考实现） |
| nexus-core 镜像 | 直接用 `modules/nexus-core/code/backend/Dockerfile` |
| `docker-compose.yml` | 发布版组装，镜像标签是 `__VERSION__`，发布时替换 |
| `honeycomb-install.sh` / `.ps1` | 一键安装 / 升级，`__TAG__` 发布时替换。`.ps1` 必须保留 UTF-8 BOM |
| `NOTES.md` | Release 页面的说明文字 |

## 怎么发一版（仓主）

1. dev 合进 main（`dev → main` 的 PR，CI 全绿）。
2. 在 main 上打标签并推：`git tag v0.2.0 && git push origin v0.2.0`。
3. `.github/workflows/release.yml` 自动：构建三张镜像（amd64 + arm64）推到
   `ghcr.io/dblsc1/honeycomb-{nexus-core,web,auth}`（标签 `0.2.0` 与 `latest`），
   建 GitHub Release 并挂上 compose 与两个安装脚本。
4. **只有第一次**：ghcr 新包缺省是私有的，匿名拉不下来。到 GitHub 个人主页 →
   Packages，把这三个包各自 Package settings → Change visibility 改成 Public。
   改完再从一台干净的机器跑一次安装脚本确认。

## 发布前怎么知道它装得起来

`ci.yml` 的「发布包」job 在每个 PR 上：本地构建同样三张镜像，用同一个安装脚本
（sh 与 PowerShell 7 各一遍）从空目录装、冒烟，再跑一次 sh 验升级路径。
Windows 自带的 PowerShell 5.1 没有 CI 覆盖，要真机验。
