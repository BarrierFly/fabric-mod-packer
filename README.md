# fabric-mod-packer

把多个 Minecraft Fabric mod 的 jar 打包成一个合并包：合并包本身是一个极简 Fabric mod，通过 Fabric 官方的 Jar-in-Jar（嵌套 jar）机制，把所有输入 jar 原样嵌入 `META-INF/jars/` 并在 `fabric.mod.json` 里声明加载。装上这一个 jar 就等于装上了里面的所有 mod，也可以随时解包还原。

仅依赖 Python 标准库（3.8+），无第三方包。

## 用法

### 打包

命令行参数（支持通配符；按输入顺序处理，第一个文件决定合并包的 id / name / version）：

    fabric-mod-packer pack sodium.jar "mods/lithium-*.jar" -o 合并包.jar

不带参数运行则从控制台输入一行（空格分隔多个，含空格的路径用引号括起）：

    fabric-mod-packer pack
    > sodium.jar "my mods/a mod.jar" mods/*.jar

不安装直接跑也可以：`python -m fabric_mod_packer pack ...`

输出默认写到当前目录 `<合并id>.jar`；同名文件已存在会报错，可用 `-o` 指定。

### 解包

    fabric-mod-packer unpack 合并包.jar -o 输出目录

把合并包 `fabric.mod.json` 里 `jars` 字段声明的嵌套 jar 全部解到输出目录（默认 `./<jar主名>-unpacked/`）。

## 合并规则

- **id / name / version**：`<第一个的值>-and-<其余数量>-more`，例如输入 3 个 mod、第一个 id 为 `sodium` → `sodium-and-2-more`。拼接结果超过官方 id 64 字符上限时，自动改用「首 id 截断 + 内容哈希」的兜底 id 并打印警告（不同 mod 组合的兜底 id 不相同）。
- **description / contact / authors / license / icon**：一律省略（Fabric 规范允许缺省）。
- **environment**：取交集（`*` 表示 client + server）。交集为空（例如同时有 client-only 和 server-only 的 mod）→ 记为 `*` 并打印警告：Fabric Loader 并不按 environment 拒绝加载，但请自行确认各 mod 在对应端可正常工作；不认识的取值仍报错。
- **depends**：取交集——只保留所有输入都声明的依赖；版本要求尽量自动合并（`*` 消解、同向下界/上界取最严、上下界拼成区间、精确匹配可满足时保留），合并不了就保留第一个输入的写法并打印警告。没被共同声明的依赖直接丢弃——每个嵌套 mod 自己的 depends 仍会被 Fabric Loader 正常校验，约束不会丢。各输入对 `minecraft` / `fabricloader` 的版本要求写法不一致时会打印提示（不阻断）——不同 MC 版本的 mod 混着打包是常见错误。
- **冲突检查**：mod id 重复、输入文件重名直接报错；**嵌套重复也直接报错**——某个输入 jar 自己又嵌套（Jar-in-Jar）了另一个输入，或两个输入嵌套了同一个库，这种包装好后启动必然因 mod id 重复而崩溃；不同输入里有相同路径的文件（排除每个 mod 必有的 fabric.mod.json 与 META-INF/）打印警告但继续。打包摘要会列出每个输入自带嵌套的 mod，方便核对。
- 打包完成后程序会重新打开产物自检（CRC、元数据可读、id 合法、声明的嵌套 jar 齐全）。

## 会报错的情况

输入无法识别为 Fabric mod jar（缺 fabric.mod.json、schemaVersion 不是 1、id/version 不合法、depends 值不是字符串或字符串数组等）、输入 jar 内部 CRC 校验失败、嵌套 mod id 重复（见上）、只有 1 个输入 jar、通配符无匹配、输出文件已存在、输出目录不存在等——均打印中文错误并以非零码退出。

## 使用注意

- **分发他人 mod 前先确认许可**：多数 mod 禁止或限制再分发，重打包分发前需逐个查看其 license。
- **不要重复安装**：合并包里的 mod 若又单独装了一份（或装了两个包含同一 mod 的合并包），Fabric Loader 会报 mod id 重复、游戏无法启动。
- **组件无法单独更新/禁用**：想升级或停用其中一个 mod，只能解包后单独管理，或用新输入重新打包。
- **合并不解决冲突**：合并包只是把 jar 原样嵌在一起，不隔离也不调和 mod 间的任何不兼容。

## 开发

跑冒烟测试（无需安装任何依赖）：

    python tests/smoke_test.py

## 明确不做

- 完整的 semver 区间运算（只做上述受限合并，其余回退取第一个 + 警告）
- 合并 jar 内容本身（只做官方嵌套，不做 class / 资源合并）
- recommends / suggests / breaks / conflicts 等软依赖字段的合并
