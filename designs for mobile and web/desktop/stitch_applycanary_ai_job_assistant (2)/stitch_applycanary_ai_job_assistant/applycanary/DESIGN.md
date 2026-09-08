---
name: ApplyCanary
colors:
  surface: '#f8f9fa'
  surface-dim: '#d9dadb'
  surface-bright: '#f8f9fa'
  surface-container-lowest: '#ffffff'
  surface-container-low: '#f3f4f5'
  surface-container: '#edeeef'
  surface-container-high: '#e7e8e9'
  surface-container-highest: '#e1e3e4'
  on-surface: '#191c1d'
  on-surface-variant: '#484456'
  inverse-surface: '#2e3132'
  inverse-on-surface: '#f0f1f2'
  outline: '#797488'
  outline-variant: '#cac3d9'
  surface-tint: '#642afa'
  primary: '#5000e1'
  on-primary: '#ffffff'
  primary-container: '#6933ff'
  on-primary-container: '#e5dcff'
  inverse-primary: '#cbbeff'
  secondary: '#705d00'
  on-secondary: '#ffffff'
  secondary-container: '#fcd400'
  on-secondary-container: '#6e5c00'
  tertiary: '#853100'
  on-tertiary: '#ffffff'
  tertiary-container: '#ac4200'
  on-tertiary-container: '#ffd9ca'
  error: '#ba1a1a'
  on-error: '#ffffff'
  error-container: '#ffdad6'
  on-error-container: '#93000a'
  primary-fixed: '#e7deff'
  primary-fixed-dim: '#cbbeff'
  on-primary-fixed: '#1e0061'
  on-primary-fixed-variant: '#4b00d4'
  secondary-fixed: '#ffe16d'
  secondary-fixed-dim: '#e9c400'
  on-secondary-fixed: '#221b00'
  on-secondary-fixed-variant: '#544600'
  tertiary-fixed: '#ffdbcd'
  tertiary-fixed-dim: '#ffb596'
  on-tertiary-fixed: '#360f00'
  on-tertiary-fixed-variant: '#7c2e00'
  background: '#f8f9fa'
  on-background: '#191c1d'
  surface-variant: '#e1e3e4'
typography:
  display-lg:
    fontFamily: Inter
    fontSize: 32px
    fontWeight: '700'
    lineHeight: 40px
    letterSpacing: -0.02em
  headline-lg:
    fontFamily: Inter
    fontSize: 24px
    fontWeight: '700'
    lineHeight: 32px
  headline-md:
    fontFamily: Inter
    fontSize: 20px
    fontWeight: '600'
    lineHeight: 28px
  body-lg:
    fontFamily: Inter
    fontSize: 16px
    fontWeight: '400'
    lineHeight: 24px
  body-md:
    fontFamily: Inter
    fontSize: 14px
    fontWeight: '400'
    lineHeight: 20px
  label-md:
    fontFamily: Inter
    fontSize: 12px
    fontWeight: '600'
    lineHeight: 16px
    letterSpacing: 0.05em
rounded:
  sm: 0.25rem
  DEFAULT: 0.5rem
  md: 0.75rem
  lg: 1rem
  xl: 1.5rem
  full: 9999px
spacing:
  base: 4px
  xs: 4px
  sm: 8px
  md: 16px
  lg: 24px
  xl: 32px
  container-padding: 16px
  gutter: 12px
---

## Brand & Style

The design system is engineered to project the persona of an tireless, intelligent agent. It balances the high-tech capabilities of AI with the professional reliability required for career advancement. The "Safe" visual identity ensures that despite the advanced technology powering the backend, the interface remains grounded, trustworthy, and highly legible.

The aesthetic follows a **Corporate Modern** approach—prioritizing clarity and systematic organization while using intentional splashes of color and depth to highlight AI-driven insights. The goal is to make complex data (match scores, application status, AI feedback) feel organized and manageable, reducing the cognitive load on the job seeker.

## Colors

The palette is anchored by a deep Indigo primary color, representing the "agentic" intelligence and stability of the platform. A sharp Canary Yellow is used as a strategic accent color for high-visibility highlights, calls to action, and AI-driven notifications.

- **Primary (#6933FF):** Used for main branding, primary buttons, and active states.
- **Accent (#FFD700):** Used sparingly for "AI Insights," special badges, and drawing attention to the most critical information.
- **Surface:** A clean, light-mode foundation using off-whites and soft grays to maintain a "professional utility" feel.
- **Score System:** A specialized gradient from Red to Green is used specifically for job-matching scores, providing immediate visual feedback on application suitability.

## Typography

This design system utilizes **Inter** across all levels to maintain a systematic, utilitarian, and highly readable interface. The type hierarchy is designed for "data-heavy but organized" screens.

- **Headlines:** Use Bold (700) or Semi-Bold (600) weights to create a clear information hierarchy, ensuring job titles and section headers are immediately scannable.
- **Body Text:** Uses a standard 16px size for primary content to ensure accessibility on mobile devices.
- **Labels:** Small, uppercase labels with slightly increased letter spacing are used for metadata like "Experience Level" or "Posted Date."
- **Mobile Scaling:** Headline sizes should be reduced by 15-20% on small viewports while maintaining their weight to preserve the "bold" look without overwhelming the screen.

## Layout & Spacing

The layout philosophy is based on an **8pt grid system** tailored for mobile-first environments. 

- **Grid Model:** A fluid column system with a standard 16px margin on mobile devices. 
- **Organization:** Content is grouped into logical card containers. Vertical spacing between cards should be consistent (16px), while internal card padding should be 16px to maintain density without feeling cramped.
- **Dividers:** Subtle 1px borders (#E9ECEF) are used within cards to separate data points (e.g., job requirements vs. benefits) without adding visual noise.

## Elevation & Depth

To evoke the "high-tech" feel, the system uses **Ambient Shadows** and **Tonal Layers** rather than heavy borders.

- **Surface Levels:** The main background is the lowest level. Card containers sit one level above with a very soft, diffused shadow (0px 4px 20px rgba(0, 0, 0, 0.05)).
- **Active States:** Elements being interacted with or AI "suggestions" may use a slightly higher elevation or a subtle inner glow using the primary purple color.
- **Gradients:** Subtle linear gradients (e.g., Primary Purple to a slightly deeper shade) are applied to primary action buttons to give them a tactile, premium feel.

## Shapes

The shape language is **Rounded**, utilizing a base radius of 8px (0.5rem) for standard components like input fields and buttons. 

- **Cards:** Larger containers like job cards and AI feedback panels use a 16px (1rem) radius (`rounded-lg`) to appear approachable and modern.
- **Badges:** Score indicators and status tags use a full pill-shape (999px) to distinguish them from interactive buttons.
- **Visual Consistency:** Every interactive element must adhere to these roundedness rules to maintain the cohesive, modern brand identity.

## Components

### Buttons & Controls
- **Primary Button:** Deep Purple background, White text, 8px border radius, subtle gradient.
- **AI Action Button:** Yellow background, Dark text, used exclusively for AI-driven features like "Tailor Resume."
- **Checkboxes/Radios:** Use the Primary Purple for the "checked" state for high brand recognition.

### Cards & Lists
- **Job Cards:** White background, 16px border radius, 1px soft border. Job titles are Headline-MD, Match Scores are displayed in the top-right corner using a pill-shaped badge.
- **Lists:** Clean, high-contrast rows with 16px vertical padding and 1px bottom dividers.

### AI Specific Components
- **Score Badges:** Pill-shaped with a background color reflecting the Match Gradient (Red, Yellow, or Green) and high-contrast text.
- **AI Interview Studio:** Features a "Voice Wave" animation component—a series of vertical bars that animate with varying heights to indicate the AI is listening/processing.
- **Tailoring Progress:** A thin linear progress bar using the primary purple color with a "sparkle" icon at the leading edge to indicate AI-assisted movement.

### Inputs & Fields
- **Search Bars:** High-contrast borders with a "Canary" yellow focus ring to emphasize the active search for jobs.
- **Feedback Toasts:** Small, floating alerts with 8px rounded corners, using semantic colors for success or info messages.