# Testing GlobalAnalyticsAssistant Component

## Manual Testing Instructions

1. **Start the development server:**
   ```bash
   cd ui
   npm run dev
   ```

2. **Open the application in your browser:**
   - Navigate to `http://localhost:3000`
   - You should see the publisher UI with authentication

3. **Test the Analytics Assistant:**

   ### Basic Functionality
   - Look for the "Analytics Assistant" floating button in the bottom-right corner
   - Click the button to open the chat interface
   - Verify the modal opens with the chat interface

   ### Page Context Detection
   - Navigate to different pages:
     - Home (`/`) - Should show "Home Dashboard" context
     - Analytics (`/dashboard`) - Should show "Analytics Dashboard" context  
     - Content (`/content/add`) - Should show "Content Management" context
     - Bot Control (`/bot-control`) - Should show "Bot Control" context
   - Each page should display the appropriate context badge and greeting

   ### Chat Functionality
   - Type a message in the input field
   - Press Enter or click Send
   - Verify the message appears in the chat
   - Wait for the simulated assistant response
   - Check that suggestions appear below assistant messages

   ### UI Controls
   - Test the minimize button (should minimize to a small indicator)
   - Test the close button (should close the modal completely)
   - Test reopening from the minimized state

   ### Quick Actions
   - Verify quick action buttons appear based on page context
   - Click a quick action button to populate the input field

## Expected Behavior

✅ **Working Features:**
- Floating button appears on all pages
- Modal opens/closes correctly
- Page context detection works
- Chat interface is functional
- Simulated responses work
- Quick actions populate input
- Minimize/restore functionality

## Component Architecture

The GlobalAnalyticsAssistant component includes:

1. **Page Context Detection** - Uses React Router to detect current page
2. **Chat Interface** - Modal-based chat with CloudScape components
3. **Simulated API** - Mock responses for testing without backend
4. **State Management** - Local state for messages, context, and UI state
5. **Responsive Design** - Works on different screen sizes

## Next Steps

After verifying the basic UI works:
1. Implement page context detection (task 10.2)
2. Add contextual chat features (task 10.3)
3. Connect to real analytics agent API (task 12)

## Notes

- The component uses CloudScape design system for consistency
- All responses are currently simulated for testing
- Authentication context is inherited from the main app
- The component is globally available on all pages