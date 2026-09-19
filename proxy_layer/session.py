import contextvars

# 用 ContextVar 传 conversation_id，避免 handler 签名掺入非业务参数
conversation_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "conversation_id", default="",
)
