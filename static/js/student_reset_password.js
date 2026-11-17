// Student Password Reset JavaScript

const resetForm = document.getElementById('resetForm');
const errorMessage = document.getElementById('error-message');
const successMessage = document.getElementById('success-message');

resetForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    
    const studentId = document.getElementById('studentId').value.trim();
    const code = document.getElementById('code').value.trim();
    const newPassword = document.getElementById('newPassword').value;
    const confirmPassword = document.getElementById('confirmPassword').value;
    
    // Hide previous messages
    errorMessage.style.display = 'none';
    successMessage.style.display = 'none';
    
    // Validate password match
    if (newPassword !== confirmPassword) {
        showError('Passwords do not match');
        return;
    }
    
    // Validate password length
    if (newPassword.length < 8) {
        showError('Password must be at least 8 characters');
        return;
    }
    
    try {
        const response = await fetch('/api/student/reset-password', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                student_id: studentId,
                code: code,
                new_password: newPassword
            })
        });
        
        const data = await response.json();
        
        if (response.ok) {
            // Hide the form and login link
            resetForm.style.display = 'none';
            document.getElementById('login-link').style.display = 'none';
            
            // Show success message with login button
            successMessage.innerHTML = `
                <h3 style="color: #2ecc71; margin-bottom: 1rem;">✅ Password Reset Successful!</h3>
                <p style="margin-bottom: 1.5rem;">Your password has been updated. Please login with your new password.</p>
                <a href="/student/login" class="btn btn-primary" style="text-decoration: none; display: inline-block; padding: 0.75rem 2rem;">
                    Go to Login Page
                </a>
            `;
            successMessage.style.display = 'block';
        } else {
            showError(errorText(data, 'Password reset failed'));
        }
    } catch (error) {
        console.error('Error:', error);
        showError('An error occurred during password reset');
    }
});

function showError(message) {
    errorMessage.textContent = message;
    errorMessage.style.display = 'block';
}
