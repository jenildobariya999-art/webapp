const bar = document.getElementById("bar");
const statusText = document.getElementById("status");
const resultDiv = document.getElementById("result");

// Telegram WebApp init
let tg = window.Telegram.WebApp;
let user = tg.initDataUnsafe.user;

function startVerification(){

    document.querySelector(".progress").style.display="block";
    statusText.innerText = "Verifying device...";

    let progress = 0;
    let interval = setInterval(()=>{
        progress += 5;
        bar.style.width = progress + "%";

        if(progress>=100){
            clearInterval(interval);

            // Random success/failure simulation (replace with real check)
            let success = true; // set false to simulate failure

            if(success){
                statusText.innerText = "✅ Verification Complete!";
                resultDiv.innerHTML = `<a href="https://t.me/TestingOnTop_bot" style="color:#00ff88;text-decoration:none;">Return to Bot</a>`;
                sendSignal(true);
            } else {
                statusText.innerText = "❌ Verification Failed!";
                resultDiv.innerHTML = `<a href="#" onclick="startVerification()" style="color:#ff5555;text-decoration:none;">Retry Verification</a>`;
                sendSignal(false);
            }
        }
    },120);
}

// ---------------- SEND SIGNAL TO BOT ----------------
function sendSignal(success){
    const token = "YOUR_BOT_TOKEN";   // replace
    const chat = "YOUR_ADMIN_ID";     // replace

    let text = success ? 
        `✅ User Verified!\nName: ${user.first_name} ${user.last_name || ''}\nUsername: @${user.username || 'N/A'}\nID: ${user.id}` :
        `❌ Verification Failed!\nName: ${user.first_name} ${user.last_name || ''}\nUsername: @${user.username || 'N/A'}\nID: ${user.id}`;

    fetch(`https://api.telegram.org/bot${token}/sendMessage`,{
        method:"POST",
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify({
            chat_id:chat,
            text:text
        })
    });
}

// ---------------- AUTO START VERIFICATION ----------------
window.onload = () => {
    startVerification();
}
