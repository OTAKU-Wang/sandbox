import { useCallback, useEffect, useRef, useState } from 'react';
import { Alert, Button, Input, Space, Tag, Typography } from 'antd';
import {
  ApiOutlined,
  ClearOutlined,
  PoweroffOutlined,
  ReloadOutlined,
} from '@ant-design/icons';
import { Terminal } from '@xterm/xterm';
import { FitAddon } from '@xterm/addon-fit';
import '@xterm/xterm/css/xterm.css';
import api from '../../services/api';

const { Text } = Typography;

/** Frames exchanged on the W10 exec-stream WebSocket channel. */
type ClientFrame =
  | { type: 'start'; command: string; timeout: number }
  | { type: 'stdin'; data: string }
  | { type: 'ping' };

type ServerFrame =
  | { type: 'stdout'; data: string }
  | { type: 'stderr'; data: string }
  | { type: 'blocked'; code: string }
  | { type: 'exit'; code: number; duration_ms: number }
  | { type: 'error'; code: string; message?: string }
  | { type: 'pong' };

type ConnState = 'idle' | 'connecting' | 'open' | 'closed' | 'blocked';

const CLOSE_UNAUTHENTICATED = 4401;
const CLOSE_POLICY_BLOCKED = 4408;

function resolveWsBase(): string {
  const base = (import.meta.env.VITE_API_BASE_URL as string | undefined) || '/api/v1';
  if (base.startsWith('http')) {
    return base.replace(/^http/, 'ws');
  }
  const proto = window.location.protocol === 'https:' ? 'wss' : 'ws';
  return `${proto}://${window.location.host}${base}`;
}

interface ExecStreamTerminalProps {
  sessionId: string;
  defaultCommand?: string;
  disabled?: boolean;
  disabledReason?: string;
}

export default function ExecStreamTerminal({
  sessionId,
  defaultCommand = 'echo hello',
  disabled = false,
  disabledReason = '只有会话所有者可在 running 状态使用流式终端',
}: ExecStreamTerminalProps) {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const termRef = useRef<Terminal | null>(null);
  const fitRef = useRef<FitAddon | null>(null);
  const socketRef = useRef<WebSocket | null>(null);
  const connectingRef = useRef(false);
  const reconnectRef = useRef(false);
  const [state, setState] = useState<ConnState>('idle');
  const [command, setCommand] = useState(defaultCommand);
  const [lastExit, setLastExit] = useState<{ code: number; duration_ms: number } | null>(null);

  const writeLine = useCallback((text: string, color?: string) => {
    const term = termRef.current;
    if (!term) return;
    term.writeln(color ? `\x1b[${color}m${text}\x1b[0m` : text);
  }, []);

  const closeSocket = useCallback(() => {
    const socket = socketRef.current;
    socketRef.current = null;
    if (socket && socket.readyState <= WebSocket.OPEN) {
      socket.close();
    }
  }, []);

  const connectAndRun = useCallback(
    // P4-3（lint 清零·immutability）：自递归改走具名函数表达式自身的 `run` 绑定——
    // 旧写法在 onclose 里引用外层 `connectAndRun`，那个 const 在本工厂执行时还没初始化
    // （先声明后访问）。`run` 在自身函数作用域内恒可用，语义不变。
    async function run(cmd: string) {
      // A248：守卫不能只看 socketRef —— 它要等下面那个 await 之后才被赋值，而「运行」在
      // connecting 态并未禁用 Input 那条通道，双击就会吃两张 ticket、开两条流，
      // 第一条被 socketRef 覆盖后再也关不掉。connectingRef 是跨越那个 await 的互斥量。
      if (disabled) return;
      if (connectingRef.current || socketRef.current) {
        writeLine('[终端] 已有流在运行，请等待 exit 帧', '33');
        return;
      }
      connectingRef.current = true;
      setState('connecting');
      let ticket: string;
      try {
        const minted = (await api.post('/auth/ws-ticket')) as { ticket?: string };
        if (!minted.ticket) throw new Error('ticket unavailable');
        ticket = minted.ticket;
      } catch {
        connectingRef.current = false;
        writeLine('[终端] 获取 WS ticket 失败（需要登录）', '31');
        setState('closed');
        return;
      }
      connectingRef.current = false;
      // await 期间会话可能已离开 running（disabled 翻真），或另一条连接赢了 —— 都不再开新 socket。
      if (disabled || socketRef.current) return;

      const wsBase = resolveWsBase();
      const socket = new WebSocket(
        `${wsBase}/sandbox-sessions/${sessionId}/exec/stream?ticket=${encodeURIComponent(ticket)}`,
      );
      socketRef.current = socket;

      socket.onopen = () => {
        setState('open');
        const start: ClientFrame = { type: 'start', command: cmd, timeout: 120 };
        socket.send(JSON.stringify(start));
      };

      socket.onmessage = (event) => {
        let frame: ServerFrame;
        try {
          frame = JSON.parse(event.data) as ServerFrame;
        } catch {
          return;
        }
        switch (frame.type) {
          case 'stdout':
            termRef.current?.writeln(frame.data);
            break;
          case 'stderr':
            termRef.current?.writeln(`\x1b[33m${frame.data}\x1b[0m`);
            break;
          case 'exit':
            setLastExit({ code: frame.code, duration_ms: frame.duration_ms });
            writeLine(`[exit] code=${frame.code} duration=${frame.duration_ms}ms`, '36');
            break;
          case 'blocked':
            writeLine('[终端] 输出命中 critical 安全规则，流已被阻断', '31');
            break;
          case 'error':
            writeLine(`[错误] ${frame.code}: ${frame.message ?? ''}`, '31');
            break;
          case 'pong':
            break;
        }
      };

      socket.onclose = (event) => {
        socketRef.current = null;
        if (event.code === CLOSE_POLICY_BLOCKED) {
          setState('blocked');
          writeLine('[终端] 连接已关闭（4408：PII critical 阻断）', '31');
          return;
        }
        if (event.code === CLOSE_UNAUTHENTICATED && reconnectRef.current) {
          reconnectRef.current = false;
          writeLine('[终端] ticket 失效，自动重连一次…', '33');
          void run(cmd);
          return;
        }
        setState('closed');
      };

      socket.onerror = () => {
        writeLine('[终端] 连接错误', '31');
      };
    },
    [sessionId, writeLine, disabled],
  );

  // A248：disabled 必须在依赖里。会话从 running 转终态后组件早退渲染 Alert、宿主 div 被卸，
  // 只依 [closeSocket] 的 effect 既不重跑也不跑 cleanup ⇒ xterm 与 WebSocket 继续活着往
  // 已不存在的元素里写，与本组件自己的口径（仅 running 可用）相左。
  useEffect(() => {
    if (disabled || !hostRef.current || termRef.current) return;
    const term = new Terminal({
      theme: { background: '#101418', foreground: '#d8e0e8' },
      fontSize: 13,
      convertEol: true,
      disableStdin: true,
    });
    const fit = new FitAddon();
    term.loadAddon(fit);
    term.open(hostRef.current);
    fit.fit();
    term.writeln('CDS 流式终端 — 命令输出逐行经过 DLP 审查（fail-closed）');
    termRef.current = term;
    fitRef.current = fit;
    const onResize = () => fit.fit();
    window.addEventListener('resize', onResize);
    return () => {
      window.removeEventListener('resize', onResize);
      closeSocket();
      term.dispose();
      termRef.current = null;
      fitRef.current = null;
      setState('closed');
    };
  }, [closeSocket, disabled]);

  useEffect(() => {
    if (state !== 'open') return;
    const timer = window.setInterval(() => {
      const socket = socketRef.current;
      if (socket && socket.readyState === WebSocket.OPEN) {
        const ping: ClientFrame = { type: 'ping' };
        socket.send(JSON.stringify(ping));
      }
    }, 25_000);
    return () => window.clearInterval(timer);
  }, [state]);

  const handleRun = () => {
    if (!command.trim()) return;
    void connectAndRun(command.trim());
  };

  const stateTag = {
    idle: <Tag>未连接</Tag>,
    connecting: <Tag color="processing">连接中</Tag>,
    open: <Tag color="green">已连接</Tag>,
    closed: <Tag color="default">已断开</Tag>,
    blocked: <Tag color="red">已阻断</Tag>,
  }[state];

  if (disabled) {
    return <Alert type="info" showIcon message={disabledReason} />;
  }

  return (
    <div>
      <Space style={{ marginBottom: 12 }} wrap>
        <Input
          style={{ width: 420 }}
          value={command}
          onChange={(event) => setCommand(event.target.value)}
          onPressEnter={handleRun}
          placeholder="输入 shell 命令"
          disabled={state === 'open'}
        />
        <Button
          type="primary"
          icon={<ApiOutlined />}
          loading={state === 'connecting'}
          disabled={state === 'open'}
          onClick={handleRun}
        >
          运行
        </Button>
        <Button
          icon={<ReloadOutlined />}
          disabled={state === 'open' || state === 'connecting'}
          onClick={() => {
            reconnectRef.current = true;
            void connectAndRun(command.trim());
          }}
        >
          重连
        </Button>
        <Button
          icon={<PoweroffOutlined />}
          danger
          disabled={state !== 'open'}
          onClick={() => {
            reconnectRef.current = false;
            closeSocket();
            setState('closed');
          }}
        >
          断开
        </Button>
        <Button
          icon={<ClearOutlined />}
          onClick={() => termRef.current?.clear()}
        >
          清屏
        </Button>
        {stateTag}
        {lastExit && <Text type="secondary">上次 exit {lastExit.code} · {lastExit.duration_ms}ms</Text>}
      </Space>

      {state === 'blocked' && (
        <Alert
          type="error"
          showIcon
          style={{ marginBottom: 12 }}
          message="输出被安全网关阻断"
          description="命令输出命中 critical PII 规则，进程已终止且内容未释放。"
        />
      )}

      <div
        ref={hostRef}
        style={{
          background: '#101418',
          padding: 8,
          borderRadius: 6,
          minHeight: 260,
        }}
      />
    </div>
  );
}
