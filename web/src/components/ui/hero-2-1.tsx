"use client";

import { useState } from "react";
import { Link } from "react-router-dom";
import { ArrowRight, Menu, X } from "lucide-react";
import { motion, AnimatePresence } from "motion/react";

/**
 * Landing hero for KDP Niche Intelligence.
 *
 * Adapted from the hero-2-1 pattern: the same gradient-and-grain field, the
 * same nav/mobile-menu behaviour, with the copy, links and image swapped for
 * this product. Navigation goes straight to the dashboard tools; the hero
 * image is a stock Unsplash photograph of a bookshop shelf.
 */

const TOOLS: { label: string; tab: string }[] = [
  { label: "Niche Research", tab: "research" },
  { label: "Discovery", tab: "discover" },
  { label: "Trend Radar", tab: "trends" },
  { label: "Teardown", tab: "teardown" },
];

const HERO_IMAGE =
  "https://images.unsplash.com/photo-1512820790803-83ca734da794?auto=format&fit=crop&w=1920&q=75";

const Hero2 = () => {
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);

  return (
    <div className="relative min-h-screen overflow-hidden bg-black">
      {/* Gradient background with grain effect */}
      <div className="flex flex-col items-end absolute -right-60 -top-10 blur-xl z-0 ">
        <div className="h-[10rem] rounded-full w-[60rem] z-1 bg-gradient-to-b blur-[6rem] from-purple-600 to-sky-600"></div>
        <div className="h-[10rem] rounded-full w-[90rem] z-1 bg-gradient-to-b blur-[6rem] from-pink-900 to-yellow-400"></div>
        <div className="h-[10rem] rounded-full w-[60rem] z-1 bg-gradient-to-b blur-[6rem] from-yellow-600 to-sky-500"></div>
      </div>
      <div className="absolute inset-0 z-0 bg-noise opacity-30"></div>

      {/* Content container */}
      <div className="relative z-10">
        {/* Navigation */}
        <nav className="container mx-auto flex items-center justify-between px-4 py-4 mt-6">
          <Link to="/" className="flex items-center">
            <div className="flex h-8 w-8 items-center justify-center rounded-full bg-white text-black">
              <span className="font-bold">📚</span>
            </div>
            <span className="ml-2 text-xl font-bold text-white">KDP Niche Intelligence</span>
          </Link>

          {/* Desktop Navigation */}
          <div className="hidden md:flex items-center space-x-6">
            <div className="flex items-center space-x-6">
              {TOOLS.map((t) => (
                <NavItem key={t.tab} label={t.label} to={`/app?tab=${t.tab}`} />
              ))}
              <NavItem label="Calculator" to="/app?tab=calc" />
            </div>
            <div className="flex items-center space-x-3">
              <Link
                to="/app"
                className="inline-flex h-12 items-center rounded-full bg-white px-8 text-base font-medium text-black hover:bg-white/90"
              >
                Open dashboard
              </Link>
            </div>
          </div>

          {/* Mobile menu button */}
          <button
            className="md:hidden"
            onClick={() => setMobileMenuOpen(!mobileMenuOpen)}
          >
            <span className="sr-only">Toggle menu</span>
            {mobileMenuOpen ? (
              <X className="h-6 w-6 text-white" />
            ) : (
              <Menu className="h-6 w-6 text-white" />
            )}
          </button>
        </nav>

        {/* Mobile Navigation Menu with animation */}
        <AnimatePresence>
          {mobileMenuOpen && (
            <motion.div
              initial={{ y: "-100%" }}
              animate={{ y: 0 }}
              exit={{ y: "-100%" }}
              transition={{ duration: 0.3 }}
              className="fixed inset-0 z-50 flex flex-col p-4 bg-black/95 md:hidden"
            >
              <div className="flex items-center justify-between">
                <div className="flex items-center">
                  <div className="flex h-8 w-8 items-center justify-center rounded-full bg-white text-black">
                    <span className="font-bold">📚</span>
                  </div>
                  <span className="ml-2 text-xl font-bold text-white">
                    KDP Niche Intelligence
                  </span>
                </div>
                <button onClick={() => setMobileMenuOpen(false)}>
                  <X className="h-6 w-6 text-white" />
                </button>
              </div>
              <div className="mt-8 flex flex-col space-y-6">
                {TOOLS.map((t) => (
                  <MobileNavItem key={t.tab} label={t.label} to={`/app?tab=${t.tab}`} />
                ))}
                <MobileNavItem label="Royalty Calculator" to="/app?tab=calc" />
                <Link
                  to="/app"
                  className="inline-flex h-12 items-center justify-center rounded-full bg-white px-8 text-base font-medium text-black hover:bg-white/90"
                >
                  Open dashboard
                </Link>
              </div>
            </motion.div>
          )}
        </AnimatePresence>

        {/* Badge */}
        <div className="mx-auto mt-6 flex max-w-fit items-center justify-center space-x-2 rounded-full bg-white/10 px-4 py-2 backdrop-blur-sm">
          <span className="text-sm font-medium text-white">
            Every number traced to a live Amazon page
          </span>
          <ArrowRight className="h-4 w-4 text-white" />
        </div>

        {/* Hero section */}
        <div className="container mx-auto mt-12 px-4 text-center">
          <h1 className="mx-auto max-w-4xl text-5xl font-bold leading-tight text-white md:text-6xl lg:text-7xl">
            Find the KDP niche that pays before you write a word
          </h1>
          <p className="mx-auto mt-6 max-w-2xl text-lg text-gray-300">
            Keyword demand, category entry bars, income bands with named confidence,
            review mining, and reverse-ASIN teardowns. Built on Amazon's own pages,
            so an estimate is always shown as a range, never a promise.
          </p>
          <div className="mt-10 flex flex-col items-center justify-center space-y-4 sm:flex-row sm:space-x-4 sm:space-y-0">
            <Link
              to="/app"
              className="inline-flex h-12 items-center rounded-full bg-white px-8 text-base font-medium text-black hover:bg-white/90"
            >
              Start a niche scan
            </Link>
            <Link
              to="/app?demo=1"
              className="inline-flex h-12 items-center rounded-full border border-gray-600 px-8 text-base font-medium text-white hover:bg-white/10"
            >
              See a sample scan
            </Link>
          </div>

          <div className="relative mx-auto my-20 w-full max-w-6xl">
            <div className="absolute inset-0 rounded shadow-lg bg-white blur-[10rem] bg-grainy opacity-20" />

            {/* Hero Image */}
            <img
              src={HERO_IMAGE}
              alt="Shelves of books in a bookshop"
              className="relative w-full h-auto shadow-md grayscale-100 rounded"
            />
          </div>
        </div>
      </div>
    </div>
  );
};

function NavItem({ label, to }: { label: string; to: string }) {
  return (
    <Link to={to} className="flex items-center text-sm text-gray-300 hover:text-white">
      <span>{label}</span>
    </Link>
  );
}

function MobileNavItem({ label, to }: { label: string; to: string }) {
  return (
    <Link
      to={to}
      className="flex items-center justify-between border-b border-gray-800 pb-2 text-lg text-white"
    >
      <span>{label}</span>
      <ArrowRight className="h-4 w-4 text-gray-400" />
    </Link>
  );
}

export { Hero2 };
