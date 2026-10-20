"use client";

import { Button, Flex, Tag, Typography } from "antd";
import { CustomerServiceOutlined, LogoutOutlined } from "@ant-design/icons";
import { useRouter } from "next/navigation";

export function SiteHeader({ role }: { role?: "customer" | "agent" }) {
  const router = useRouter();

  function logout() {
    sessionStorage.removeItem("orl_token");
    sessionStorage.removeItem("orl_role");
    router.push("/login");
  }

  return (
    <Flex
      align="center"
      justify="space-between"
      style={{
        width: "100%",
        maxWidth: 1200,
        margin: "0 auto",
        padding: "6px 4px",
      }}
    >
      <Flex align="center" gap={10}>
        <CustomerServiceOutlined style={{ fontSize: 22, color: "#22356F" }} />
        <Typography.Text strong style={{ fontSize: 18, color: "#1c2430" }}>
          星辰数码客服
        </Typography.Text>
        {role ? (
          <Tag color={role === "agent" ? "#22356F" : "#2A9D8F"} style={{ marginInlineEnd: 0 }}>
            {role === "agent" ? "人工客服" : "顾客"}
          </Tag>
        ) : null}
      </Flex>
      {role ? (
        <Button type="text" icon={<LogoutOutlined />} onClick={logout}>
          退出
        </Button>
      ) : null}
    </Flex>
  );
}
