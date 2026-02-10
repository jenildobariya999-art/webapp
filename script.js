const bar = document.getElementById("bar");
const statusText = document.getElementById("status");
const resultDiv = document.getElementById("result");

// Telegram WebApp init
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

            // Simulate success (replace with real check)
            let success = true;

            if (success) {
                statusText.innerText = "✅ Verification Complete!";

                // Send signal to bot
                sendSignal(true);

                // Close WebApp and return to bot automatically
                setTimeout(() => {
                    tg.close();  // This will return user to Telegram chat
                }, 1000);

            } else {
                statusText.innerText = "❌ Verification Failed!";
                resultDiv.innerHTML = `<a href="#" onclick="startVerification()" style="color:#ff5555;text-decoration:none;">Retry Verification</a>`;
                sendSignal(false);
            }
        }
    }, 120);
}

// Send signal to bot
function sendSignal(success) {
    const token = "YOUR_BOT_TOKEN";   // Replace
    const chat = "YOUR_ADMIN_ID";     // Replace

    let text = success ?
        `✅ User Verified!\nName: ${user.first_name}\nUsername: @${user.username || 'N/A'}\nID: ${user.id}` :
        `❌ Verification Failed!\nName: ${user.first_name}\nUsername: @${user.username || 'N/A'}\nID: ${user.id}`;

    fetch(`https://api.telegram.org/bot${token}/sendMessage`, {
        method: "POST",
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ chat_id: chat, text: text })
    });
}

// Auto start verification
window.onload = () => {
    startVerification();
}
