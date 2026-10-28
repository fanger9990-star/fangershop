import logging
import secrets
import smtplib
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.contrib import messages as django_messages
from django.contrib.auth import get_user_model, login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.mail import send_mail
from django.db import transaction
from django.db.models import Case, F, Q, Sum, When
from django.shortcuts import get_object_or_404, render, redirect
from django.templatetags.static import static
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from .forms import AccountProfileForm, CheckoutForm, ProductForm, SellerProfileForm, SignupCodeForm, SignupCompleteForm, SignupEmailForm
from .models import Category, Message, MessageThread, NewsletterSubscriber, Order, OrderItem, Product, ProductImage, SellerProfile, Wishlist


SORT_OPTIONS = {
    'newest': '-created_at',
    'price_asc': 'price',
    'price_desc': '-price',
    'name': 'name',
}
logger = logging.getLogger(__name__)
MAX_COMPARE_PRODUCTS = 4
MAX_RECENTLY_VIEWED = 20


def get_recently_viewed_ids(request):
    viewed_ids = request.session.get('recently_viewed', [])
    if not isinstance(viewed_ids, list):
        viewed_ids = []
    sanitized_ids = []
    for value in viewed_ids:
        try:
            product_id = int(value)
        except (TypeError, ValueError):
            continue
        if product_id not in sanitized_ids:
            sanitized_ids.append(product_id)
    request.session['recently_viewed'] = sanitized_ids[:MAX_RECENTLY_VIEWED]
    return sanitized_ids[:MAX_RECENTLY_VIEWED]


def get_recommendations(request, limit=4):
    viewed_ids = get_recently_viewed_ids(request)
    if not viewed_ids:
        return Product.objects.none()
    viewed_products = Product.objects.filter(id__in=viewed_ids).exclude(category__isnull=True)
    category_ids = list(
        viewed_products.values_list('category_id', flat=True).distinct()[:MAX_RECENTLY_VIEWED]
    )
    if not category_ids:
        return Product.objects.none()
    category_order = Case(
        *[When(category_id=category_id, then=index) for index, category_id in enumerate(category_ids)],
        output_field=Product._meta.get_field('category').target_field,
    )
    return Product.objects.filter(category_id__in=category_ids, stock__gt=0).exclude(
        id__in=viewed_ids
    ).select_related('category', 'seller').prefetch_related('images').order_by(
        category_order,
        '-is_featured',
        '-created_at',
    )[:limit]


def get_compare_ids(request):
    compare_ids = request.session.get('compare_products', [])
    if not isinstance(compare_ids, list):
        compare_ids = []
    sanitized_ids = []
    for value in compare_ids:
        try:
            product_id = int(value)
        except (TypeError, ValueError):
            continue
        if product_id not in sanitized_ids:
            sanitized_ids.append(product_id)
    request.session['compare_products'] = sanitized_ids[:MAX_COMPARE_PRODUCTS]
    return sanitized_ids[:MAX_COMPARE_PRODUCTS]


def get_cart(request):
    cart = request.session.get('cart', {})
    if not isinstance(cart, dict):
        cart = {}
    request.session['cart'] = cart
    return cart


def cart_context(request):
    cart = get_cart(request)
    items = []
    subtotal = Decimal('0')

    for product_id, quantity in cart.items():
        try:
            product_id = int(product_id)
            qty = int(quantity)
        except (TypeError, ValueError):
            continue
        product = Product.objects.filter(id=product_id).first()
        if product is None:
            continue
        qty = min(max(1, qty), product.stock)
        if qty == 0:
            continue
        item_total = product.price * qty
        subtotal += item_total
        items.append({'product': product, 'quantity': qty, 'item_total': item_total})

    return {'cart_items': items, 'subtotal': subtotal}


def get_wishlist(request):
    if request.user.is_authenticated:
        product_ids = list(Wishlist.objects.filter(user=request.user).values_list('product_id', flat=True))
        return product_ids

    wishlist = request.session.get('wishlist', [])
    if not isinstance(wishlist, list):
        wishlist = []
    request.session['wishlist'] = wishlist
    return wishlist


def home(request):
    categories = Category.objects.all()[:6]
    featured_products = Product.objects.filter(is_featured=True)[:3]
    if not featured_products:
        featured_products = Product.objects.all()[:3]

    context = {
        'categories': categories,
        'featured_products': featured_products,
        'recommended_products': get_recommendations(request),
    }
    return render(request, 'shop/home.html', context)


def product_list(request):
    products = Product.objects.select_related('category', 'seller').prefetch_related('images')
    category_slug = request.GET.get('category', '').strip()
    query = request.GET.get('q', '').strip()
    min_price = request.GET.get('min_price', '').strip()
    max_price = request.GET.get('max_price', '').strip()
    city = request.GET.get('city', '').strip()
    sort_key = request.GET.get('sort', 'newest').strip()

    if category_slug:
        products = products.filter(category__slug=category_slug)
    if query:
        products = products.filter(Q(name__icontains=query) | Q(description__icontains=query))
    if min_price:
        try:
            products = products.filter(price__gte=Decimal(min_price))
        except InvalidOperation:
            pass
    if max_price:
        try:
            products = products.filter(price__lte=Decimal(max_price))
        except InvalidOperation:
            pass
    if city:
        products = products.filter(seller__seller_profile__city__icontains=city)

    products = products.order_by(SORT_OPTIONS.get(sort_key, '-created_at'))

    categories = Category.objects.all()
    cities = SellerProfile.objects.exclude(city='').values_list('city', flat=True).distinct().order_by('city')
    current_category = Category.objects.filter(slug=category_slug).first() if category_slug else None
    wishlist_ids = get_wishlist(request)
    compare_ids = get_compare_ids(request)

    return render(request, 'shop/product_list.html', {
        'products': products,
        'categories': categories,
        'cities': cities,
        'current_category': current_category,
        'query': query,
        'selected_city': city,
        'selected_sort': sort_key,
        'min_price': min_price,
        'max_price': max_price,
        'wishlist_ids': wishlist_ids,
        'compare_ids': compare_ids,
    })


def category_detail(request, slug):
    category = get_object_or_404(Category, slug=slug)
    products = Product.objects.filter(category=category).select_related('category', 'seller').prefetch_related('images')
    categories = Category.objects.all()
    return render(request, 'shop/product_list.html', {
        'products': products,
        'categories': categories,
        'current_category': category,
        'compare_ids': get_compare_ids(request),
        'wishlist_ids': get_wishlist(request),
    })


def product_detail(request, slug):
    product = get_object_or_404(Product, slug=slug)
    viewed_ids = get_recently_viewed_ids(request)
    viewed_ids = [product_id for product_id in viewed_ids if product_id != product.id]
    request.session['recently_viewed'] = [product.id, *viewed_ids][:MAX_RECENTLY_VIEWED]
    request.session.modified = True
    gallery = []
    for item in product.images.all():
        gallery.append({'url': item.image.url if item.image else '', 'caption': item.caption or product.name})

    if not gallery and product.image:
        gallery = [{'url': static(product.image), 'caption': product.name}]

    seller = getattr(product.seller, 'seller_profile', None) if product.seller else None
    if seller is None:
        seller = SellerProfile.objects.filter(business_name__isnull=False).order_by('-created_at').first()

    related_products = Product.objects.filter(category=product.category).exclude(id=product.id)[:3]
    wishlist_ids = get_wishlist(request)
    seller_products = Product.objects.filter(seller=product.seller) if product.seller_id else Product.objects.none()
    seller_sales = OrderItem.objects.filter(
        product__seller=product.seller,
        order__status__in=['confirmed', 'shipped', 'completed'],
    ) if product.seller_id else OrderItem.objects.none()
    return render(request, 'shop/product_detail.html', {
        'product': product,
        'gallery': gallery,
        'related_products': related_products,
        'seller': seller,
        'seller_product_count': seller_products.count(),
        'seller_sales_count': seller_sales.aggregate(total=Sum('quantity'))['total'] or 0,
        'wishlist_ids': wishlist_ids,
        'compare_ids': get_compare_ids(request),
    })


def signup_email(request):
    if request.method == 'POST':
        form = SignupEmailForm(request.POST)
        if form.is_valid():
            email = form.cleaned_data['email'].strip().lower()
            user_model = get_user_model()
            if user_model.objects.filter(email__iexact=email).exists():
                form.add_error('email', 'This email is already registered.')
            else:
                code = f'{secrets.randbelow(900000) + 100000}'
                try:
                    sent_count = send_mail(
                        subject='NovaCart email verification code',
                        message=f'Your NovaCart verification code is: {code}',
                        from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', 'no-reply@novacart.local'),
                        recipient_list=[email],
                        fail_silently=False,
                    )
                except (OSError, smtplib.SMTPException):
                    logger.exception('Could not send NovaCart signup verification email.')
                    form.add_error(
                        None,
                        'We could not send the verification email. Check the email service configuration and try again.',
                    )
                else:
                    if sent_count != 1:
                        form.add_error(
                            None,
                            'The email service did not accept the verification email. Please try again.',
                        )
                    else:
                        request.session['signup_email'] = email
                        request.session['signup_code'] = code
                        request.session['signup_verified'] = False
                        request.session['signup_code_created_at'] = timezone.now().timestamp()
                        request.session['signup_code_attempts'] = 0
                        request.session.modified = True
                        return redirect('signup_verify')
    else:
        form = SignupEmailForm()
    return render(request, 'registration/signup_email.html', {'form': form})


def signup_verify(request):
    email = request.session.get('signup_email')
    if not email:
        return redirect('signup_email')

    if request.method == 'POST':
        form = SignupCodeForm(request.POST)
        if form.is_valid():
            submitted_code = form.cleaned_data['code'].strip()
            actual_code = request.session.get('signup_code')
            created_at = request.session.get('signup_code_created_at', 0)
            attempts = request.session.get('signup_code_attempts', 0)
            expired = timezone.now().timestamp() - created_at > 600
            if attempts >= 5 or expired:
                request.session.pop('signup_code', None)
                request.session.pop('signup_code_created_at', None)
                request.session.pop('signup_code_attempts', None)
                request.session.modified = True
                form.add_error('code', 'This verification code has expired. Please request a new one.')
            elif actual_code and secrets.compare_digest(submitted_code, actual_code):
                request.session['signup_verified'] = True
                request.session.modified = True
                return redirect('signup_complete')
            else:
                request.session['signup_code_attempts'] = attempts + 1
                request.session.modified = True
                form.add_error('code', 'The verification code is incorrect.')
    else:
        form = SignupCodeForm()

    return render(request, 'registration/signup_verify.html', {'form': form, 'email': email})


def signup_complete(request):
    email = request.session.get('signup_email')
    if not email or not request.session.get('signup_verified'):
        return redirect('signup_email')

    if request.method == 'POST':
        form = SignupCompleteForm(request.POST)
        if form.is_valid():
            username = form.cleaned_data['username']
            password = form.cleaned_data['password']
            user_model = get_user_model()
            try:
                validate_password(password, user=user_model(username=username, email=email))
            except ValidationError as error:
                form.add_error('password', error)
                return render(request, 'registration/signup_complete.html', {'form': form, 'email': email})
            user = user_model.objects.create_user(username=username, email=email, password=password)
            SellerProfile.objects.get_or_create(
                user=user,
                defaults={
                    'business_name': username,
                    'city': 'Not set',
                    'phone': '',
                    'bio': 'New seller on NovaCart.',
                    'rating': 4.8,
                },
            )
            login(request, user)
            request.session.pop('signup_email', None)
            request.session.pop('signup_code', None)
            request.session.pop('signup_verified', None)
            request.session.pop('signup_code_created_at', None)
            request.session.pop('signup_code_attempts', None)
            request.session.modified = True
            return redirect('account')
    else:
        form = SignupCompleteForm()

    return render(request, 'registration/signup_complete.html', {'form': form, 'email': email})


@require_POST
def wishlist_toggle(request, slug):
    product = get_object_or_404(Product, slug=slug)
    wishlist = get_wishlist(request)
    product_id = product.id
    if request.user.is_authenticated:
        saved = Wishlist.objects.filter(user=request.user, product=product).first()
        if saved:
            saved.delete()
        else:
            Wishlist.objects.get_or_create(user=request.user, product=product)
    else:
        if product_id in wishlist:
            wishlist = [item for item in wishlist if item != product_id]
        else:
            wishlist.append(product_id)
        request.session['wishlist'] = wishlist
        request.session.modified = True

    next_url = request.META.get('HTTP_REFERER', '')
    if next_url and url_has_allowed_host_and_scheme(
        next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return redirect(next_url)
    return redirect('product_list')


def wishlist(request):
    wishlist_ids = get_wishlist(request)
    products = Product.objects.filter(id__in=wishlist_ids).select_related('category').prefetch_related('images')
    return render(request, 'shop/wishlist.html', {'products': products, 'wishlist_ids': wishlist_ids})


@require_POST
def compare_toggle(request, slug):
    product = get_object_or_404(Product, slug=slug)
    compare_ids = get_compare_ids(request)
    if product.id in compare_ids:
        compare_ids.remove(product.id)
        django_messages.success(request, f'{product.name} removed from comparison.')
    elif len(compare_ids) >= MAX_COMPARE_PRODUCTS:
        django_messages.warning(request, f'Compare up to {MAX_COMPARE_PRODUCTS} products at a time.')
    else:
        compare_ids.append(product.id)
        django_messages.success(request, f'{product.name} added to comparison.')

    request.session['compare_products'] = compare_ids
    request.session.modified = True
    next_url = request.POST.get('next', '')
    if next_url and url_has_allowed_host_and_scheme(
        next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return redirect(next_url)
    return redirect('product_compare')


def product_compare(request):
    compare_ids = get_compare_ids(request)
    products = Product.objects.filter(id__in=compare_ids).select_related(
        'category',
        'seller',
    ).prefetch_related('images')
    product_order = Case(
        *[When(pk=product_id, then=index) for index, product_id in enumerate(compare_ids)],
        output_field=Product._meta.pk,
    )
    products = list(products.order_by(product_order))
    available_products = [product for product in products if product.stock > 0]
    best_price_product = min(
        available_products or products,
        key=lambda item: item.price,
    ) if products else None
    return render(request, 'shop/compare.html', {
        'products': products,
        'compare_ids': compare_ids,
        'best_price_id': best_price_product.id if best_price_product else None,
        'most_stocked_id': max(products, key=lambda item: item.stock).id if products else None,
        'compare_limit': MAX_COMPARE_PRODUCTS,
    })


@login_required
def messages(request):
    product_slug = request.GET.get('product') or request.POST.get('product')
    seller_id = request.GET.get('seller') or request.POST.get('seller')
    thread_id = request.GET.get('thread')
    buyer_user = request.user
    product = get_object_or_404(Product, slug=product_slug) if product_slug else None
    seller = None
    if seller_id:
        seller_model = get_user_model()
        seller = seller_model.objects.filter(pk=seller_id).first()

    if request.method == 'POST' and request.POST.get('message'):
        content = request.POST.get('message', '').strip()
        thread_id = request.POST.get('thread_id')
        thread = None

        if thread_id:
            thread = get_object_or_404(
                MessageThread.objects.filter(Q(buyer=request.user) | Q(seller=request.user)),
                id=thread_id,
            )
        elif product and seller and product.seller_id == seller.id and seller != buyer_user:
            thread, _ = MessageThread.objects.get_or_create(
                buyer=buyer_user,
                seller=seller,
                product=product,
            )

        if thread and content and len(content) <= 5000:
            thread = get_object_or_404(
                MessageThread.objects.filter(Q(buyer=request.user) | Q(seller=request.user)),
                id=thread.id,
            )
            Message.objects.create(thread=thread, sender=request.user, content=content)
            thread.save(update_fields=['updated_at'])
            return redirect(f'{reverse("messages")}?thread={thread.id}')
        if content and len(content) > 5000:
            django_messages.error(request, 'Messages must be 5,000 characters or fewer.')
        elif content and not thread:
            django_messages.error(request, 'This conversation could not be opened.')

    threads = MessageThread.objects.filter(
        Q(buyer=request.user) | Q(seller=request.user)
    ).select_related('buyer', 'seller', 'product').order_by('-updated_at')

    active_thread = None
    if thread_id:
        active_thread = get_object_or_404(threads, id=thread_id)
    if product and seller and product.seller_id == seller.id and seller != buyer_user:
        active_thread = MessageThread.objects.filter(
            buyer=buyer_user,
            seller=seller,
            product=product,
        ).first()
        thread_list = list(threads)
        if active_thread and active_thread not in thread_list:
            thread_list.insert(0, active_thread)
        threads = thread_list
    elif threads.exists():
        active_thread = threads.first()

    if active_thread and active_thread not in threads:
        thread_list = list(threads)
        thread_list.insert(0, active_thread)
        threads = thread_list

    return render(request, 'shop/messages.html', {
        'threads': threads,
        'active_thread': active_thread,
        'product': product,
        'seller': seller,
    })


@login_required
def create_listing(request):
    if request.method == 'POST':
        form = ProductForm(request.POST, request.FILES)
        if form.is_valid():
            uploaded_files = form.cleaned_data.get('images', [])
            if len(uploaded_files) > 8:
                form.add_error('images', 'You can upload up to 8 images per product.')
            else:
                product = form.save(commit=False)
                if request.user.is_authenticated:
                    product.seller = request.user
                product.save()

                for index, uploaded_file in enumerate(uploaded_files[:8]):
                    ProductImage.objects.create(
                        product=product,
                        image=uploaded_file,
                        is_primary=index == 0,
                    )

                if product.image and product.image.startswith('shop/'):
                    product.image = product.image
                elif not product.image and uploaded_files:
                    product.image = uploaded_files[0].name
                product.save(update_fields=['image'])
                return redirect('product_detail', slug=product.slug)
    else:
        form = ProductForm()

    return render(request, 'shop/create_listing.html', {'form': form})


def pricing(request):
    plans = [
        {
            'name': 'Starter',
            'price': '$19',
            'description': 'For new sellers launching their first collection.',
            'features': ['1 Storefront', 'Basic analytics', 'Email support'],
            'highlight': False,
        },
        {
            'name': 'Growth',
            'price': '$49',
            'description': 'Great for growing brands and frequent product listings.',
            'features': ['Unlimited listings', 'Promo campaigns', 'Priority support'],
            'highlight': True,
        },
        {
            'name': 'Pro',
            'price': '$99',
            'description': 'Built for high-volume sellers and premium storefronts.',
            'features': ['Advanced insights', 'Custom offers', 'Dedicated account manager'],
            'highlight': False,
        },
    ]
    return render(request, 'shop/pricing.html', {'plans': plans})


def profile(request):
    if request.user.is_authenticated:
        profile_obj = SellerProfile.objects.filter(user=request.user).first()
        if profile_obj is None:
            profile_obj = SellerProfile(business_name=request.user.username)
    else:
        profile_obj = SellerProfile.objects.first()

    if profile_obj is None:
        profile_obj = SellerProfile(
            business_name='NovaCart Studio',
            city='Tehran',
            phone='+989121234567',
            bio='Showcasing premium products with fast shipping and exceptional service.',
            rating=4.9,
        )

    products = Product.objects.filter(seller=request.user) if request.user.is_authenticated else Product.objects.all()[:4]
    return render(request, 'shop/profile.html', {'profile': profile_obj, 'products': products})


@login_required
def account(request):
    wishlist_ids = get_wishlist(request)
    viewed_ids = get_recently_viewed_ids(request)
    recent_products = Product.objects.filter(id__in=viewed_ids).select_related(
        'category',
    ).prefetch_related('images')
    recent_order = Case(
        *[When(pk=product_id, then=index) for index, product_id in enumerate(viewed_ids)],
        output_field=Product._meta.pk,
    )
    orders = Order.objects.filter(user=request.user).prefetch_related('items__product')[:8]
    saved_products = Product.objects.filter(id__in=wishlist_ids).select_related(
        'category',
    ).prefetch_related('images')[:4]
    threads = MessageThread.objects.filter(
        Q(buyer=request.user) | Q(seller=request.user)
    ).select_related('buyer', 'seller', 'product')[:5]
    thread_count = MessageThread.objects.filter(
        Q(buyer=request.user) | Q(seller=request.user)
    ).count()
    return render(request, 'shop/account.html', {
        'account_user': request.user,
        'orders': orders,
        'saved_products': saved_products,
        'recent_products': recent_products.order_by(recent_order)[:4] if viewed_ids else recent_products[:4],
        'threads': threads,
        'thread_count': thread_count,
        'recommendations': get_recommendations(request),
        'wishlist_count': len(wishlist_ids),
        'compare_count': len(get_compare_ids(request)),
        'order_count': Order.objects.filter(user=request.user).count(),
    })


@login_required
def update_account(request):
    if request.method == 'POST':
        form = AccountProfileForm(request.POST, instance=request.user)
        if form.is_valid():
            form.save()
            django_messages.success(request, 'Your personal profile has been updated.')
            return redirect('account')
    else:
        form = AccountProfileForm(instance=request.user)
    return render(request, 'shop/account_edit.html', {'form': form})


@login_required
def update_profile(request):
    profile_obj = SellerProfile.objects.filter(user=request.user).first()

    if request.method == 'POST':
        form = SellerProfileForm(request.POST, instance=profile_obj)
        if form.is_valid():
            profile_obj = form.save(commit=False)
            profile_obj.user = request.user
            profile_obj.save()
            return redirect('profile')
    else:
        form = SellerProfileForm(
            instance=profile_obj,
            initial={'business_name': request.user.username},
        )

    return render(request, 'shop/update_profile.html', {'form': form})


def cart(request):
    cart_data = cart_context(request)
    cart_items = cart_data['cart_items']
    subtotal = cart_data['subtotal']
    shipping = Decimal('0') if subtotal >= Decimal('200') else Decimal('12')
    total = subtotal + shipping

    return render(request, 'shop/cart.html', {
        'cart_items': cart_items,
        'subtotal': subtotal,
        'shipping': shipping,
        'total': total,
    })


def checkout(request):
    cart_data = cart_context(request)
    cart_items = cart_data['cart_items']
    if not cart_items:
        return redirect('cart')

    subtotal = cart_data['subtotal']
    shipping = Decimal('0') if subtotal >= Decimal('200') else Decimal('12')
    total = subtotal + shipping
    initial = {}
    if request.user.is_authenticated:
        initial = {
            'customer_name': request.user.get_full_name() or request.user.username,
            'customer_email': request.user.email,
        }
    checkout_form = CheckoutForm(request.POST or None, initial=initial)

    if request.method == 'POST':
        if not checkout_form.is_valid():
            return render(request, 'shop/checkout.html', {
                'cart_items': cart_items,
                'subtotal': subtotal,
                'shipping': shipping,
                'total': total,
                'form': checkout_form,
            })
        customer_name = checkout_form.cleaned_data['customer_name']
        customer_email = checkout_form.cleaned_data['customer_email']
        customer_phone = checkout_form.cleaned_data['customer_phone']
        address = checkout_form.cleaned_data['address']

        try:
            with transaction.atomic():
                locked_items = []
                for item in cart_items:
                    product = Product.objects.select_for_update().get(pk=item['product'].pk)
                    if item['quantity'] > product.stock:
                        raise ValidationError(
                            f'Only {product.stock} item(s) of {product.name} are currently available.'
                        )
                    locked_items.append((product, item['quantity']))

                subtotal = sum(
                    (product.price * quantity for product, quantity in locked_items),
                    Decimal('0'),
                )
                shipping = Decimal('0') if subtotal >= Decimal('200') else Decimal('12')
                total = subtotal + shipping

                for product, quantity in locked_items:
                    updated = Product.objects.filter(
                        pk=product.pk,
                        stock__gte=quantity,
                    ).update(stock=F('stock') - quantity)
                    if updated != 1:
                        raise ValidationError(
                            f'{product.name} is no longer available in the requested quantity.'
                        )

                order = Order.objects.create(
                    user=request.user if request.user.is_authenticated else None,
                    customer_name=customer_name,
                    customer_email=customer_email,
                    customer_phone=customer_phone,
                    address=address,
                    total=total,
                    status='pending',
                )

                for product, quantity in locked_items:
                    OrderItem.objects.create(
                        order=order,
                        product=product,
                        quantity=quantity,
                        price=product.price,
                    )
        except ValidationError as error:
            django_messages.error(request, error.messages[0])
            return redirect('cart')

        request.session['cart'] = {}
        request.session['last_order_id'] = order.id
        request.session.modified = True
        return redirect('checkout_success', order_id=order.id)

    return render(request, 'shop/checkout.html', {
        'cart_items': cart_items,
        'subtotal': subtotal,
        'shipping': shipping,
        'total': total,
        'form': checkout_form,
    })


def checkout_success(request, order_id):
    order = get_object_or_404(Order, id=order_id)
    if order.user_id:
        if not request.user.is_authenticated or order.user_id != request.user.id:
            return redirect('home')
    elif request.session.get('last_order_id') != order.id:
        return redirect('home')
    return render(request, 'shop/checkout_success.html', {'order': order})


@login_required
def seller_dashboard(request):
    user = request.user
    products = Product.objects.filter(seller=user).select_related('category')
    sales_items = OrderItem.objects.filter(
        product__seller=user,
        order__status__in=['confirmed', 'shipped', 'completed'],
    )

    total_listings = products.count()
    total_sales = sales_items.count()
    total_revenue = sum((item.price * item.quantity) for item in sales_items)

    return render(request, 'shop/seller_dashboard.html', {
        'products': products,
        'total_listings': total_listings,
        'total_sales': total_sales,
        'total_revenue': total_revenue,
        'recent_orders': Order.objects.filter(items__product__seller=user).distinct()[:5],
    })


@require_POST
def add_to_cart(request, slug):
    product = get_object_or_404(Product, slug=slug)
    if product.stock < 1:
        django_messages.error(request, 'This product is currently out of stock.')
        return redirect('product_detail', slug=slug)
    cart = get_cart(request)
    product_id = str(product.id)
    try:
        current_quantity = int(cart.get(product_id, 0))
    except (TypeError, ValueError):
        current_quantity = 0
    cart[product_id] = min(max(0, current_quantity) + 1, product.stock)
    request.session['cart'] = cart
    request.session.modified = True
    return redirect('cart')


@require_POST
def update_cart_item(request, slug):
    product = get_object_or_404(Product, slug=slug)
    cart = get_cart(request)
    if request.method == 'POST':
        try:
            quantity = int(request.POST.get('quantity', 1))
        except (TypeError, ValueError):
            django_messages.error(request, 'Enter a valid quantity.')
            return redirect('cart')
        if quantity <= 0:
            cart.pop(str(product.id), None)
        elif product.stock < 1:
            cart.pop(str(product.id), None)
            django_messages.warning(request, 'This product is currently out of stock.')
        else:
            cart[str(product.id)] = min(quantity, product.stock)
            if quantity > product.stock:
                django_messages.warning(request, f'Only {product.stock} item(s) are currently available.')
        request.session['cart'] = cart
        request.session.modified = True
    return redirect('cart')


@require_POST
def remove_from_cart(request, slug):
    product = get_object_or_404(Product, slug=slug)
    cart = get_cart(request)
    cart.pop(str(product.id), None)
    request.session['cart'] = cart
    request.session.modified = True
    return redirect('cart')


@require_POST
def newsletter_subscribe(request):
    email = request.POST.get('email', '').strip()
    form = SignupEmailForm({'email': email})
    if form.is_valid():
        NewsletterSubscriber.objects.get_or_create(email=form.cleaned_data['email'].lower())
        django_messages.success(request, 'Thank you for your interest in NovaCart.')
    else:
        django_messages.error(request, form.errors['email'][0])
    return redirect('home')
