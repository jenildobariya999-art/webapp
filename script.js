const bar = document.getElementById("bar");
const statusText = document.getElementById("status");
const resultDiv = document.getElementById("result");

let tg = window.Telegram.WebApp;
let user = tg.initDataUnsafe.user;

function startVerification() {
    document.querySelector(".progress").style.display = "block";
    statusText.innerText = "Verifying device...";
    let progress = 0;

    let interval = setInterval(() => {
        progress += 5;
        bar.style.width = progress + "%";

        if (progress >= 100) {
            clearInterval(interval);
            let success = true; // replace with real check

            if (success) {
                statusText.innerText = "✅ Verification Complete!";

                // Send signal to bot
                fetch(`https://api.telegram.org/botYOUR_BOT_TOKEN/sendMessage`, {
                    method: "POST",
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        chat_id: user.id,
                        text: "/verified_signal"
                    })
                });

                // Close WebApp
                setTimeout(()=>{ tg.close(); }, 1000);

            } else {
                statusText.innerText = "❌ Verification Failed!";
                resultDiv.innerHTML = `<a href="#" onclick="startVerification()" style="color:#ff5555;text-decoration:none;">Retry Verification</a>`;
            }
        }
    },120);
}

window.onload = () => { startVerification(); }
