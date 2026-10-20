import "./globals.css";
import type { ReactNode } from "react";
import { AntdRegistry } from "@ant-design/nextjs-registry";
import { ConfigProvider } from "antd";
import zhCN from "antd/locale/zh_CN";

export const metadata = {
  title: "星辰数码客服",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="zh-CN">
      <body>
        <AntdRegistry>
          <ConfigProvider
            locale={zhCN}
            theme={{
              token: {
                colorPrimary: "#22356F",
                colorSuccess: "#2A9D8F",
                colorBgLayout: "#f5f7fa",
                colorText: "#1c2430",
                colorBorder: "#d9e0e7",
                borderRadius: 10,
                fontFamily:
                  "Inter, 'Segoe UI', 'PingFang SC', 'Microsoft YaHei', sans-serif",
              },
            }}
          >
            {children}
          </ConfigProvider>
        </AntdRegistry>
      </body>
    </html>
  );
}
