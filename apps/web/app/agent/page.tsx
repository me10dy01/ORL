"use client";

import { FormEvent, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { Badge, Button, Card, Empty, Flex, Input, List, Space, Tag, Typography, message } from "antd";
import {
  CheckCircleOutlined,
  CheckOutlined,
  ClockCircleOutlined,
  SendOutlined,
} from "@ant-design/icons";
import { api } from "@/lib/api";
import { SiteHeader } from "@/components/SiteHeader";

type Ticket = {
  id: string;
  priority: string;
  status: string;
  summary: string;
  category: string;
};

type TicketMessage = { role: string; content: string };
type TicketEvent = {
  id: string;
  type: string;
  from_status: string | null;
  to_status: string | null;
};

type Profile = {
  name: string | null;
  phone: string | null;
  last_product_id: string | null;
  last_ticket_summary: string | null;
};

const STATUS_LABEL: Record<string, { text: string; color: string }> = {
  open: { text: "待接单", color: "#e9c46a" },
  in_progress: { text: "处理中", color: "#22356F" },
  resolved: { text: "已处理", color: "#2A9D8F" },
  closed: { text: "已结案", color: "#64748b" },
  cancelled: { text: "已取消", color: "#64748b" },
};

export default function AgentPage() {
  const router = useRouter();
  const [tickets, setTickets] = useState<Ticket[]>([]);
  const [closedTickets, setClosedTickets] = useState<Ticket[]>([]);
  const [ready, setReady] = useState(false);
  const [selected, setSelected] = useState("");
  const [messages, setMessages] = useState<TicketMessage[]>([]);
  const [events, setEvents] = useState<TicketEvent[]>([]);
  const [profile, setProfile] = useState<Profile | null>(null);
  const [status, setStatus] = useState("");
  const [reply, setReply] = useState("");
  const [messageApi, contextHolder] = message.useMessage();

  const selectedRef = useRef("");

  useEffect(() => {
    if (!sessionStorage.getItem("orl_token")) {
      router.push("/login");
      return;
    }
    if (sessionStorage.getItem("orl_role") !== "agent") {
      router.push("/chat");
      return;
    }
    const tick = async () => {
      await refresh();
      if (selectedRef.current) await openTicket(selectedRef.current);
    };
    tick();
    const timer = setInterval(tick, 3000);
    return () => clearInterval(timer);
  }, [router]);

  async function refresh() {
    const open = await api("/api/tickets?status=open");
    const active = await api("/api/tickets?status=in_progress");
    const resolved = await api("/api/tickets?status=resolved");
    const closed = await api("/api/tickets?status=closed");
    setTickets([...open.tickets, ...active.tickets, ...resolved.tickets]);
    setClosedTickets(closed.tickets || []);
    setReady(true);
  }

  async function openTicket(id: string) {
    selectedRef.current = id;
    setSelected(id);
    const detail = await api(`/api/tickets/${id}`);
    setStatus(detail.ticket.status);
    setMessages(detail.messages);
    setEvents(detail.events || []);
    setProfile(detail.profile || null);
  }

  async function claim() {
    try {
      await api(`/api/tickets/${selected}/claim`, { method: "POST" });
      await openTicket(selected);
      await refresh();
    } catch (err) {
      messageApi.error(err instanceof Error ? err.message : "接单失败");
    }
  }

  async function sendReply(event: FormEvent) {
    event.preventDefault();
    try {
      await api(`/api/tickets/${selected}/messages`, {
        method: "POST",
        body: JSON.stringify({ content: reply }),
      });
      setReply("");
      await openTicket(selected);
    } catch (err) {
      messageApi.error(err instanceof Error ? err.message : "回复失败");
    }
  }

  async function transition(action: string) {
    try {
      await api(`/api/tickets/${selected}/transition`, {
        method: "POST",
        body: JSON.stringify({ action }),
      });
      await openTicket(selected);
      await refresh();
    } catch (err) {
      messageApi.error(err instanceof Error ? err.message : "操作失败");
    }
  }

  const isReadOnly = status === "closed" || status === "cancelled";

  return (
    <main className="shell">
      {contextHolder}
      <SiteHeader role="agent" />
      <div className="workspace">
        <Card className="sidebar" styles={{ body: { padding: 12 } }}>
          {ready && tickets.length === 0 ? (
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无工单" style={{ margin: "40px 0" }} />
          ) : null}
          <List
            dataSource={tickets}
            locale={{ emptyText: null }}
            renderItem={(ticket) => {
              const meta = STATUS_LABEL[ticket.status] || { text: ticket.status, color: "#64748b" };
              return (
                <List.Item
                  onClick={() => openTicket(ticket.id)}
                  style={{
                    cursor: "pointer",
                    padding: "10px 12px",
                    borderRadius: 10,
                    marginBottom: 6,
                    background: ticket.id === selected ? "#eef2ff" : "transparent",
                    border: ticket.id === selected ? "1px solid #c7d2fe" : "1px solid transparent",
                  }}
                >
                  <Flex vertical gap={4} style={{ width: "100%" }}>
                    <Flex gap={6} align="center">
                      <Tag color={meta.color}>{meta.text}</Tag>
                      {ticket.priority === "high" ? <Tag color="#b91c1c">高优先级</Tag> : null}
                    </Flex>
                    <Typography.Text type="secondary" ellipsis>
                      {ticket.summary || ticket.category}
                    </Typography.Text>
                  </Flex>
                </List.Item>
              );
            }}
          />
          {closedTickets.length ? (
            <>
              <Typography.Text type="secondary" style={{ display: "block", margin: "16px 0 8px" }}>
                已结案
              </Typography.Text>
              <List
                dataSource={closedTickets}
                locale={{ emptyText: null }}
                renderItem={(ticket) => (
                  <List.Item
                    onClick={() => openTicket(ticket.id)}
                    style={{
                      cursor: "pointer",
                      padding: "10px 12px",
                      borderRadius: 10,
                      marginBottom: 6,
                      background: ticket.id === selected ? "#eef2ff" : "transparent",
                      border: ticket.id === selected ? "1px solid #c7d2fe" : "1px solid transparent",
                    }}
                  >
                    <Flex vertical gap={4} style={{ width: "100%" }}>
                      <Tag color="#64748b">已结案</Tag>
                      <Typography.Text type="secondary" ellipsis>
                        {ticket.summary || ticket.category}
                      </Typography.Text>
                    </Flex>
                  </List.Item>
                )}
              />
            </>
          ) : null}
        </Card>
        <Card className="content" styles={{ body: { display: "flex", flexDirection: "column", gap: 12 } }}>
          {selected ? (
            <>
              <Flex align="center" gap={8}>
                <Typography.Text strong>状态</Typography.Text>
                <Badge color={(STATUS_LABEL[status] || { color: "#64748b" }).color} text={(STATUS_LABEL[status] || { text: status }).text} />
              </Flex>
              {profile ? (
                <Card size="small" title="顾客档案">
                  <Flex vertical gap={4}>
                    {profile.name ? <Typography.Text>姓名：{profile.name}</Typography.Text> : null}
                    {profile.phone ? <Typography.Text>手机号：{profile.phone}</Typography.Text> : null}
                    {profile.last_product_id ? (
                      <Typography.Text>最近商品：{profile.last_product_id}</Typography.Text>
                    ) : null}
                    {profile.last_ticket_summary ? (
                      <Typography.Text>上一张工单：{profile.last_ticket_summary}</Typography.Text>
                    ) : null}
                  </Flex>
                </Card>
              ) : null}
              <div className="bubble-list">
                {messages.map((message, index) => (
                  <div className={`bubble ${message.role}`} key={`${message.role}-${index}`}>
                    <span className="meta">{roleLabel(message.role)}</span>
                    <div>{renderText(message.content)}</div>
                  </div>
                ))}
              </div>
              {events.length ? (
                <Card size="small" title="工单记录">
                  <Flex vertical gap={4}>
                    {events.map((item) => (
                      <Typography.Text key={item.id} type="secondary">
                        {item.type}
                        {item.from_status || item.to_status
                          ? ` ${item.from_status || "—"} → ${item.to_status || "—"}`
                          : ""}
                      </Typography.Text>
                    ))}
                  </Flex>
                </Card>
              ) : null}
              <Space wrap>
                <Button icon={<ClockCircleOutlined />} onClick={claim} disabled={status !== "open"}>
                  接单
                </Button>
                <Button icon={<CheckOutlined />} onClick={() => transition("resolve")} disabled={status !== "in_progress"}>
                  处理完成
                </Button>
                <Button icon={<CheckCircleOutlined />} onClick={() => transition("close")} disabled={status !== "resolved"}>
                  结案
                </Button>
              </Space>
              {!isReadOnly ? (
                <form onSubmit={sendReply}>
                  <Flex gap={8}>
                    <Input.TextArea
                      value={reply}
                      onChange={(event) => setReply(event.target.value)}
                      autoSize={{ minRows: 2, maxRows: 4 }}
                      placeholder="写回复"
                    />
                    <Button type="primary" htmlType="submit" icon={<SendOutlined />}>
                      回复
                    </Button>
                  </Flex>
                </form>
              ) : null}
            </>
          ) : (
            <Empty description="选择一张工单" style={{ margin: "60px auto" }} />
          )}
        </Card>
      </div>
    </main>
  );
}

function roleLabel(role: string) {
  if (role === "user") return "顾客";
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
