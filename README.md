# Finch

Finder 风格的分栏（Miller columns）文件浏览器，Python + GTK 3，
为 Linux Mint Cinnamon 写的。定位是**浏览、预览、打开，外加改名和回收站**，
复制、移动这些操作交给 Nemo。操作方式见 [doc/manual.md](doc/manual.md)。

## 构建与运行

依赖只有系统自带的 PyGObject（`python3-gi`、`gir1.2-gtk-3.0`），Mint 默认已装；
构建还需要 gettext 工具（`sudo apt install gettext`）。

```bash
./build.sh
```

产物在 `output/`：

- `output/finch` — 单文件可执行程序（Python zipapp，代码和翻译都打在里面），直接运行或复制到 `~/.local/bin/`
- `output/finch.desktop` — 应用菜单入口，Exec 指向上面的可执行文件

```bash
output/finch              # 打开上次的目录（首次为家目录）
output/finch ~/Pictures   # 打开指定目录
cp output/finch.desktop ~/.local/share/applications/
```

开发时不用构建，直接从源码跑：

```bash
src/Finch/finch.py
```

## 目录结构

```
build.sh                    构建脚本：编译翻译、打包成 output/finch
src/Finch/                  Finch 本体
  finch.py                  从源码启动用的脚本
  app/__init__.py           常量、翻译加载
  app/util.py               排序、状态文件、对话框、启动外部程序
  app/column.py             一栏目录列表：加载、改名、回收站操作、按键
  app/preview.py            最右侧的文件预览
  app/sidebar.py            侧边栏：分区标题、设备空间占用条
  app/window.py             主窗口：标题栏、导航历史、右键菜单、状态栏
  app/application.py        Gtk.Application、快捷键、命令行
  app/style.css             覆盖主题的样式
  app/locale/               编译好的翻译（update.sh 生成，不入库）
  po/                       翻译模板、各语言 po、update.sh
  finch.desktop             应用菜单入口模板（build.sh 填入路径）
doc/                        说明文档
output/                     构建产物，不入库
```

## 语言

界面文字用 gettext 做了本地化，源码里是英文，跟随系统语言切换；目前只带了简体中文。
翻译在 `src/Finch/po/zh_CN.po`。改了源码里的文字或翻译后运行：

```bash
src/Finch/po/update.sh   # 重新抽取 finch.pot、合并 po、编译 mo 到 src/Finch/app/locale/
```

`build.sh` 会自动先做这一步，再把翻译打进可执行文件。
想临时换语言可以用环境变量，例如 `LANGUAGE=zh_CN output/finch`。
侧边栏里 Recent、Home、Trash 这些条目由 GTK 自己翻译，需要系统装了对应的语言包
（Mint 上是 `language-pack-gnome-zh-hans`）才会变成中文。
