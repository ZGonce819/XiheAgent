"""Agent 后端能力包。

各文件职责：
- ``local_shell.py``：实现 DeepAgents 本地 Windows backend，负责文件读写、命令执行、
  Gitee Git 认证、虚拟路径映射和安全边界。
- ``workspace.py``：封装工作区根目录和路径解析，保证所有相对路径最终都落在工作区内。
- ``permissions.py``：提供更底层的路径、命令、Git 分支名和提交信息安全校验。
- ``opensandbox.py``：基于 OpenSandbox 的远程沙盒 backend 实现，提供内核级隔离。

讲课时要强调：
Prompt 只能告诉模型"应该怎么做"，但 backend 才决定模型"实际能做什么"。
这个包里的代码就是 Agent 写代码、读文件、执行命令时的最后一道工程边界。
"""

from __future__ import annotations
