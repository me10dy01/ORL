"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Button, Card, Empty, Flex, Input, List, Tag, Typography, message } from "antd";
import { PlusOutlined, SendOutlined, TeamOutlined } from "@ant-design/icons";
import { API_URL, api } from "@/lib/api";
import { SiteHeader } from "@/components/SiteHeader";

type ChatMessage = { role: string; content: string };
type ConversationItem = { conversation_id: string; mode: string; preview: string };

export default function ChatPage() {
  const router = useRouter();
  const [conversations, setConversations] = useState<ConversationItem[]>([]);
  const [conversationId, setConversationId] = useState("");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [draft, setDraft] = useState("");
  const [status, setStatus] = useState("");
  const [loading, setLoading] = useState(true);
  const [messageApi, contextHolder] = message.useMessage();

  useEffect(() => {
    const role = sessionStorage.getItem("orl_role");
    if (!sessionStorage.getItem("orl_token")) {
      router.push("/login");
      return;
    }
    if (role === "agent") {
      router.push("/agent");
      return;
    }
    loadFirst();
  }, [router]);

  async function loadConversations() {
    const listed = await api("/api/conversations/me");
    const items = (listed.conversations || []) as ConversationItem[];
    setConversations(items);
    return items;
  }

  async function openConversation(id: string) {
    setConversationId(id);
    const detail = await api(`/api/conversations/${id}`);
    setMessages(detail.messages.map((item: ChatMessage) => ({ role: item.role, content: item.content })));
  }

  async function loadFirst() {
    setLoading(true);
    try {
      const items = await loadConversations();
      if (items[0]) {
        await openConversation(items[0].conversation_id);
      }
    } catch (err) {
      messageApi.error(err instanceof Error ? err.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }

  async function createConversation() {
    const created = await api("/api/conversations", { method: "POST" });
    await loadConversations();
    await openConversation(created.conversation_id);
    setDraft("");
    setStatus("");
  }

  async function send(text: string) {
    const content = text.trim();
    if (!content || !conversationId) return;
    setDraft("");
    setStatus("");
    setMessages((current) => [...current, { role: "user", content }]);
    const response = await fetch(`${API_URL}/api/chat/stream`, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${sessionStorage.getItem("orl_token")}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ conversation_id: conversationId, message: content }),
    });
    if (!response.ok || !response.body) {
      messageApi.error("发送失败");
      return;
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let assistant = "";
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const parts = buffer.split("\n\n");
      buffer = parts.pop() || "";
      for (const part of parts) {
        const line = part.split("\n").find((item) => item.startsWith("data: "));
        if (!line) continue;
        const event = JSON.parse(line.slice(6));
        if (event.type === "reset") {
          assistant = "";
          setMessages((current) => replaceAssistant(current, ""));
        }
        if (event.type === "status") {
          setStatus(event.message || "");
        }
        if (event.type === "token") {
          setStatus("");
          assistant += event.content;
          setMessages((current) => replaceAssistant(current, assistant));
        }
        if (event.type === "done") {
          setStatus("");
          setMessages((current) => replaceAssistant(current, event.final_answer || assistant));
        }
      }
    }
    await loadConversations();
  }

  return (
    <main className="shell">
      {contextHolder}
      <SiteHeader role="customer" />
      <div className="workspace">
        <Card className="sidebar" styles={{ body: { padding: 12 } }}>
          <Button type="primary" block icon={<PlusOutlined />} onClick={createConversation} style={{ marginBottom: 12 }}>
            新会话
          </Button>
          <List
            dataSource={conversations}
            locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无会话" /> }}
            renderItem={(item) => (
              <List.Item
                onClick={() => openConversation(item.conversation_id)}
                style={{
                  cursor: "pointer",
                  padding: "10px 12px",
                  borderRadius: 10,
                  marginBottom: 6,
                  background: item.conversation_id === conversationId ? "#eef2ff" : "transparent",
                  border: item.conversation_id === conversationId ? "1px solid #c7d2fe" : "1px solid transparent",
                }}
              >
                <Flex vertical gap={4} style={{ width: "100%" }}>
                  <Tag color={item.mode === "human" ? "#22356F" : "#2A9D8F"} style={{ width: "fit-content" }}>
                    {item.mode === "human" ? "人工" : "机器人"}
                  </Tag>
                  <Typography.Text type="secondary" ellipsis>
                    {item.preview || "空会话"}
                  </Typography.Text>
                </Flex>
              </List.Item>
            )}
          />
        </Card>
        <Card className="content" styles={{ body: { display: "flex", flexDirection: "column", gap: 12 } }}>
          {loading ? <Typography.Text type="secondary">正在加载</Typography.Text> : null}
          <div className="bubble-list">
            {messages.length === 0 && !loading ? (
              <Empty description="开始一段新对话" style={{ margin: "60px auto" }} />
            ) : (
              messages.map((message, index) => (
                <div className={`bubble ${message.role}`} key={`${message.role}-${index}`}>
                  <span className="meta">{roleLabel(message.role)}</span>
                  <div>{renderText(message.content)}</div>
                </div>
              ))
            )}
          </div>
          {status ? <div className="status-line">{status}</div> : null}
          <Flex gap={8}>
            <Input.TextArea
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              autoSize={{ minRows: 2, maxRows: 5 }}
              placeholder="输入问题，回车发送"
              onPressEnter={(event) => {
                if (!event.shiftKey) {
                  event.preventDefault();
                  send(draft);
                }
              }}
            />
          </Flex>
          <Flex gap={8} justify="flex-end">
            <Button icon={<TeamOutlined />} disabled={!conversationId} onClick={() => send("转人工")}>
              转人工
            </Button>
            <Button type="primary" icon={<SendOutlined />} disabled={!conversationId} onClick={() => send(draft)}>
              发送
            </Button>
          </Flex>
        </Card>
      </div>
    </main>
  );
}

function roleLabel(role: string) {
  if (role === "user") return "我";
  if (role === "agent") return "人工客服";
  if (role === "system") return "系统";
  return "客服";
}

function renderText(text: string) {
  const parts = text.split("**");
  return parts.map((part, index) =>
    index % 2 === 1 ? <strong key={index}>{part}</strong> : part
  );
}

function replaceAssistant(messages: ChatMessage[], content: string) {
  const next = [...messages];
  const last = next[next.length - 1];
  if (last && last.role === "assistant") {
    next[next.length - 1] = { role: "assistant", content };
    return next;
  }
  next.push({ role: "assistant", content });
  return next;
}
