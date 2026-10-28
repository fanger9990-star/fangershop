from django.urls import path

from . import views

urlpatterns = [
    path('', views.home, name='home'),
    path('signup/', views.signup_email, name='signup_email'),
    path('signup/verify/', views.signup_verify, name='signup_verify'),
    path('signup/complete/', views.signup_complete, name='signup_complete'),
    path('products/', views.product_list, name='product_list'),
    path('products/compare/', views.product_compare, name='product_compare'),
    path('products/compare/toggle/<slug:slug>/', views.compare_toggle, name='compare_toggle'),
    path('products/category/<slug:slug>/', views.category_detail, name='category_detail'),
    path('products/<slug:slug>/', views.product_detail, name='product_detail'),
    path('wishlist/', views.wishlist, name='wishlist'),
    path('wishlist/toggle/<slug:slug>/', views.wishlist_toggle, name='wishlist_toggle'),
    path('messages/', views.messages, name='messages'),
    path('cart/', views.cart, name='cart'),
    path('checkout/', views.checkout, name='checkout'),
    path('checkout/success/<int:order_id>/', views.checkout_success, name='checkout_success'),
    path('cart/add/<slug:slug>/', views.add_to_cart, name='add_to_cart'),
    path('cart/update/<slug:slug>/', views.update_cart_item, name='update_cart_item'),
    path('cart/remove/<slug:slug>/', views.remove_from_cart, name='remove_from_cart'),
    path('sell/', views.create_listing, name='create_listing'),
    path('pricing/', views.pricing, name='pricing'),
    path('profile/', views.profile, name='profile'),
    path('account/', views.account, name='account'),
    path('account/edit/', views.update_account, name='update_account'),
    path('dashboard/', views.seller_dashboard, name='seller_dashboard'),
    path('profile/edit/', views.update_profile, name='update_profile'),
    path('newsletter/', views.newsletter_subscribe, name='newsletter_subscribe'),
]
