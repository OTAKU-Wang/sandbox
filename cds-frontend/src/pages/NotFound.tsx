import { Button, Result } from 'antd';
import { Link } from 'react-router-dom';

export default function NotFound() {
  return (
    <Result
      status="404"
      title="页面不存在"
      subTitle="该地址没有对应的页面，可能是链接已过期或输入有误。"
      extra={
        <Link to="/">
          <Button type="primary">返回首页</Button>
        </Link>
      }
    />
  );
}
