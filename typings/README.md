# 本地类型声明

阶段28补`jieba/__init__.pyi`，仅声明项目两处使用的lcut单字符串参数调用，依据本机0.42.1的Tokenizer.cut/lcut与模块导出核对；返回字符串列表，不声明未用选项或使用Any兜底。不改变实际分词/可选ImportError回退，新增调用或升级时先复核声明。本地声明只供mypy使用。

仅供mypy使用，不会加载为运行时数据库驱动。`aiomysql/__init__.pyi`覆盖当前项目实际使用的0.3.2接口：池创建/获取/归还/关闭，连接和游标的await/异步上下文生命周期，以及行结果。依据已安装的aiomysql `pool.py`、`connection.py`、`cursors.py`、`utils.py`核对；没有`__getattr__ -> Any`兜底。

这是部分类型表面，不是整个库的完整stub，也不代表MySQL在线联调通过。SQL单元格和动态字典行仍为Any，因为结果取决于SQL与Schema；连接参数和资源对象不以Any替代。新增驱动用法先核对实际源码后补声明；升级驱动时复核接口和回归。正式已有的YAML/asyncpg声明由开发依赖提供。

阶段47补两处声明，用来把"第三方库无stub"从整文件忽略降级为明确表面。`pyarrow/__init__.pyi`只声明`lancedb_store`建表用到的`Schema`/`DataType`与`schema`、`utf8`、`bool_`、`int32`、`int64`、`float32`、`float64`、`timestamp`、`list_`；读表、compute与RecordBatch未声明，因为类型化代码目前不调用。`sentence_transformers/__init__.pyi`只声明`SentenceTransformer(name)`与`encode(list[str], ...)`；`encode`在numpy/torch后端返回类型不同，保持Any由调用方逐元素转float。两者都是仅mypy可见的声明，不代表可选依赖已安装或BGE/向量检索已验证。
