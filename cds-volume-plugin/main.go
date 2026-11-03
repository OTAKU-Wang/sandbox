// cds-volume-plugin — CDS 加密卷插件 (SS-10 CB-03). 薄壳入口:
// 业务实现全部在 internal/vol（package vol），本文件只负责进程入口与退出码。
package main

import (
	"os"

	"cds-volume-plugin/internal/vol"
)

func main() {
	os.Exit(vol.Run())
}
