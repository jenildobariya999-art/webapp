<?php
// 1. Ensure the request method is POST
if ($_SERVER["REQUEST_METHOD"] == "POST") {
    
    // 2. Retrieve and sanitize input data to prevent basic vulnerabilities
    $userInput = isset($_POST['userInput']) ? htmlspecialchars(trim($_POST['userInput'])) : '';

    // 3. Validate that the input is not empty
    if (empty($userInput)) {
        echo "Error: Input field cannot be empty.";
        exit;
    }

    // 4. Process the data (Example logic: generic verification check)
    if ($userInput === "VALID_CODE") {
        echo "Verification successful!";
    } else {
        echo "Verification failed. Invalid input.";
    }

} else {
    // Redirect or reject direct access attempts via GET requests
    echo "Invalid request method.";
}
?>
