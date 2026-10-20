"use client";

import { Button, Card, Form, Input, Typography, message } from "antd";
import { LockOutlined, UserOutlined } from "@ant-design/icons";
import { useRouter } from "next/navigation";
import { API_URL } from "@/lib/api";

import { SiteHeader } from "@/components/SiteHeader";

type LoginForm = { username: string; password: string };

export default function LoginPage() {
  const router = useRouter();
  const [form] = Form.useForm<LoginForm>();
  const [messageApi, contextHolder] = message.useMessage();

  async function submit(values: LoginForm) {
    const response = await fetch(`${API_URL}/api/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(values),
    });
    if (!response.ok) {
      messageApi.error("登录失败");
      return;
    }
    const body = await response.json();
    sessionStorage.setItem("orl_token", body.access_token);
    sessionStorage.setItem("orl_role", body.role);
    router.push(body.role === "agent" ? "/agent" : "/chat");
  }

  return (
    <main className="shell">
      {contextHolder}
      <SiteHeader />
      <div className="login-wrap">
        <Card style={{ width: 400, boxShadow: "0 10px 30px rgba(34, 53, 111, 0.08)" }}>
          <Typography.Title level={3} style={{ marginTop: 0 }}>
            登录
          </Typography.Title>
          <Form<LoginForm>
            form={form}
            layout="vertical"
            initialValues={{ username: "user1", password: "demo1234" }}
            onFinish={submit}
          >
            <Form.Item name="username" label="账号" rules={[{ required: true }]}>
              <Input size="large" prefix={<UserOutlined />} placeholder="账号" />
            </Form.Item>
            <Form.Item name="password" label="密码" rules={[{ required: true }]}>
              <Input.Password size="large" prefix={<LockOutlined />} placeholder="密码" />
            </Form.Item>
            <Button type="primary" htmlType="submit" size="large" block>
              进入
            </Button>
          </Form>
          <Typography.Paragraph type="secondary" style={{ marginTop: 16, marginBottom: 0 }}>
            顾客 user1，人工客服 agent1，密码 demo1234。
          </Typography.Paragraph>
        </Card>
      </div>
    </main>
  );
}
