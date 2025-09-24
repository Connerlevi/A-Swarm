#!/bin/bash
# A-SWARM Mission Control Dashboard Setup Script

set -e

echo "🚀 Setting up A-SWARM Mission Control Dashboard..."

# Check if Node.js is installed
if ! command -v node &> /dev/null; then
    echo "❌ Node.js not found. Please install Node.js 18+ first."
    echo "   Download from: https://nodejs.org/"
    exit 1
fi

# Check Node.js version
NODE_VERSION=$(node --version | cut -d'v' -f2 | cut -d'.' -f1)
if [ "$NODE_VERSION" -lt 18 ]; then
    echo "❌ Node.js version 18+ required. Current version: $(node --version)"
    exit 1
fi

echo "✅ Node.js $(node --version) detected"

# Check if we're in the right directory
if [ ! -f "package.json" ]; then
    echo "❌ package.json not found. Run this script from the dashboard directory."
    exit 1
fi

# Install dependencies
echo "📦 Installing dependencies..."
npm install

# Create missing shadcn/ui components
echo "🎨 Setting up UI components..."
mkdir -p src/components/ui

# Create a simple setup for the required components since we can't run shadcn init easily
cat > src/components/ui/button.tsx << 'EOF'
import React from 'react'
import { cn } from '@/lib/utils'

interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: 'default' | 'destructive' | 'outline' | 'secondary' | 'ghost' | 'link'
  size?: 'default' | 'sm' | 'lg' | 'icon'
}

const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant = 'default', size = 'default', ...props }, ref) => {
    const variants = {
      default: 'bg-primary text-primary-foreground hover:bg-primary/90',
      destructive: 'bg-destructive text-destructive-foreground hover:bg-destructive/90',
      outline: 'border border-input bg-background hover:bg-accent hover:text-accent-foreground',
      secondary: 'bg-secondary text-secondary-foreground hover:bg-secondary/80',
      ghost: 'hover:bg-accent hover:text-accent-foreground',
      link: 'text-primary underline-offset-4 hover:underline',
    }
    
    const sizes = {
      default: 'h-10 px-4 py-2',
      sm: 'h-9 rounded-md px-3',
      lg: 'h-11 rounded-md px-8',
      icon: 'h-10 w-10',
    }

    return (
      <button
        className={cn(
          'inline-flex items-center justify-center rounded-md text-sm font-medium ring-offset-background transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:pointer-events-none disabled:opacity-50',
          variants[variant],
          sizes[size],
          className
        )}
        ref={ref}
        {...props}
      />
    )
  }
)
Button.displayName = 'Button'

export { Button }
EOF

# Create other required UI components as minimal implementations
echo "Creating UI components..."

# Let's create a quick script instead that will handle this
cat > create_ui_components.cjs << 'EOF'
const fs = require('fs');
const path = require('path');

const components = {
  'card.tsx': `import React from 'react'
import { cn } from '@/lib/utils'

const Card = React.forwardRef<HTMLDivElement, React.HTMLAttributes<HTMLDivElement>>(
  ({ className, ...props }, ref) => (
    <div ref={ref} className={cn('rounded-lg border bg-card text-card-foreground shadow-sm', className)} {...props} />
  )
)
Card.displayName = 'Card'

const CardHeader = React.forwardRef<HTMLDivElement, React.HTMLAttributes<HTMLDivElement>>(
  ({ className, ...props }, ref) => (
    <div ref={ref} className={cn('flex flex-col space-y-1.5 p-6', className)} {...props} />
  )
)
CardHeader.displayName = 'CardHeader'

const CardTitle = React.forwardRef<HTMLParagraphElement, React.HTMLAttributes<HTMLHeadingElement>>(
  ({ className, ...props }, ref) => (
    <h3 ref={ref} className={cn('text-2xl font-semibold leading-none tracking-tight', className)} {...props} />
  )
)
CardTitle.displayName = 'CardTitle'

const CardDescription = React.forwardRef<HTMLParagraphElement, React.HTMLAttributes<HTMLParagraphElement>>(
  ({ className, ...props }, ref) => (
    <p ref={ref} className={cn('text-sm text-muted-foreground', className)} {...props} />
  )
)
CardDescription.displayName = 'CardDescription'

const CardContent = React.forwardRef<HTMLDivElement, React.HTMLAttributes<HTMLDivElement>>(
  ({ className, ...props }, ref) => (
    <div ref={ref} className={cn('p-6 pt-0', className)} {...props} />
  )
)
CardContent.displayName = 'CardContent'

export { Card, CardHeader, CardTitle, CardDescription, CardContent }`,

  'use-toast.tsx': `import React from 'react'

export const useToast = () => ({
  toast: ({ title, description }: { title?: string; description?: string }) => {
    console.log('Toast:', title, description)
  }
})

export const ToastProvider = ({ children }: { children: React.ReactNode }) => <>{children}</>`,

  // Add more minimal components as needed
}

// Create the components
Object.entries(components).forEach(([filename, content]) => {
  fs.writeFileSync(path.join('src/components/ui', filename), content)
})

console.log('✅ UI components created')
EOF

node create_ui_components.cjs
rm create_ui_components.cjs

# Update .env for development
if [ ! -f ".env" ]; then
    echo "🔧 Creating development .env file..."
    cat > .env << 'EOF'
# Development configuration
VITE_ASWARM_WS_URL=ws://localhost:8000/ws
VITE_ASWARM_TOKEN=dev123
VITE_ASWARM_AUTH_MODE=query
EOF
fi

echo "✅ Dashboard setup complete!"
echo ""
echo "🎯 Quick Start:"
echo "   1. Start the backend: cd ../api && python mission_control_api.py"
echo "   2. Start the dashboard: npm run dev"
echo "   3. Visit: http://localhost:3000"
echo ""
echo "🔑 Backend auth: Set ASWARM_BEARER=dev123"
echo "📊 Dashboard will connect to: ws://localhost:8000/ws?access_token=dev123"
echo ""
echo "Run 'npm run build' to create production build."