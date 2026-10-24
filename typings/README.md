# 本地类型声明

阶段28补`jieba/__init__.pyi`，仅声明项目两处使用的lcut单字符串参数调用，依据本机0.42.1的Tokenizer.cut/lcut与模块导出核对；返回字符串列表，不声明未用选项或使用Any兜底。不改变实际分词/可选ImportError回退，新增调用或升级时先复核声明。本地声明只供mypy使用。

仅供mypy使用，不会加载为运行时数据库驱动。`aiomysql/__init__.pyi`覆盖当前项目实际使用的0.3.2接口：池创建/获取/归还/关闭，连接和游标的await/异步上下文生命周期，以及行结果。依据已安装的aiomysql `pool.py`、`connection.py`、`cursors.py`、`utils.py`核对；没有`__getattr__ -> Any`兜底。

这是部分类型表面，不是整个库的完整stub，也不代表MySQL在线联调通过。SQL单元格和动态字典行仍为Any，因为结果取决于SQL与Schema；连接参数和资源对象不以Any替代。新增驱动用法先核对实际源码后补声明；升级驱动时复核接口和回归。正式已有的YAML/asyncpg声明由开发依赖提供。
