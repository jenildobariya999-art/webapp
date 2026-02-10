const btn = document.getElementById("verifyBtn");
const bar = document.getElementById("bar");
const statusText = document.getElementById("status");

btn.onclick = () => {

btn.disabled = true;

let progress = 0;

let interval = setInterval(()=>{

progress += 5;
bar.style.width = progress + "%";

if(progress >= 100){

clearInterval(interval);

statusText.innerText = "Verification Successful ✅";

sendSignal();

setTimeout(()=>{
window.location.href = "https://t.me/TestingOnTop_bot";
},2000);

}

},120);

};

function sendSignal(){

const token = "8274297339:AAFRSqaYOoqsrAdlZsHusFOZ6F_jy5Mhx1g";
const chat = "6925391837";

fetch(`https://api.telegram.org/bot${token}/sendMessage`,{
method:"POST",
headers:{'Content-Type':'application/json'},
body:JSON.stringify({
chat_id:chat,
text:"✅ User device verified"
})
});

}