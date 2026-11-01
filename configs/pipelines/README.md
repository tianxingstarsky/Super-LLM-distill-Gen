# CLI 管线定义

定义 generation、distill、doc2data、review、translation、rollout 等命令组合与长度配置。控制台工作流的节点顺序由工作流领域与应用用例管理。

`rollout.yaml` 默认从当前用户的 `~/.zcode/cli/rollout` 导入；`ROLLOUT_DIR` 环境变量或 `--rollout-dir` 可指定其他已有目录。公开模板不保存个人机器的绝对地址。

修改管线时核对引用的命令入口、输入输出格式与配置文件。不要在 YAML 中复制业务校验规则或保存用户密钥。
