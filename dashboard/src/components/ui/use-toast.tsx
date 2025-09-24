import React from 'react'

export const useToast = () => ({
  toast: ({ title, description }: { title?: string; description?: string }) => {
    console.log('Toast:', title, description)
  }
})

export const ToastProvider = ({ children }: { children: React.ReactNode }) => <>{children}</>