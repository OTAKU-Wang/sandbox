
### [F-5] cds-frontend/src/pages/Flow/devTaskCreate.css
复述：`.ant-input-affix-wrapper` 高度写死 40px 且未排除含 textarea 的裹壳。
判定：TRUE·LATENT
证据：devTaskCreate.css:117-121 `.cds-dev-task-create .ant-form .ant-input-affix-wrapper { height: 40px; align-items: center }`（无 `input:not(textarea)`/`:has()` 区分）→ CSS 事实成立。但本屏唯一带裹壳的输入是 DevTaskDataPicker.tsx:231-236 `<Input allowClear>`（裹 `<input>`，40px 正是意图）；备注正文 DevTaskCreatePage.tsx:219-226 `<Input.TextArea rows={3} maxLength={4000}>` 未开 showCount/allowClear ⇒ 不产生 affix wrapper。断言的塌陷场景当前无页面渲染，属潜在风险（一旦有人给正文加 allowClear/showCount 即触发）。
