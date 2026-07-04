// ============================================================
// FiddlerScript — 复利笔记 satoken 自动提取
// ============================================================
// 使用方法:
//   1. 打开 Fiddler Everywhere
//   2. Rules → Customize Rules
//   3. 将以下代码粘贴到 OnBeforeRequest 函数中
//   4. 保存后，每次微信小程序发起请求时会自动提取 satoken
// ============================================================

// 在 FiddlerScript 的 OnBeforeRequest 函数中添加:
if (oSession.HostnameIs("www.fuyinkeji.top")) {
    var token = oSession.oRequest["satoken"];
    if (token && token.length > 0) {
        var fso = new ActiveXObject("Scripting.FileSystemObject");
        var filePath = "C:\\Users\\11\\.openclaw-autoclaw\\workspace\\stock-blogger-tracker\\data\\fuli_notes\\satoken.txt";
        var file = fso.CreateTextFile(filePath, true);
        file.WriteLine(token);
        file.Close();
    }
}
